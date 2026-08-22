"""In-Memory-Batching fuer Push-Benachrichtigungen ueber erledigte Aufgaben.

Ohne dieses Modul wuerde jede einzelne Aufgaben-Erledigung sofort eine
eigene Push-Nachricht an die zustaendige(n) Gruppe(n) ausloesen - bei
Mehrfach-Aktionen (Sammel-Buttons wie complete_due_tasks/complete_all_overdue,
oder mehrere schnelle Einzelabhakungen hintereinander) also z.B. 5 Pushes
statt einer zusammengefassten Meldung. Stattdessen wird hier nur eine
Erledigung "eingereiht" (queue_task_completion); das eigentliche Verschicken
uebernimmt periodisch check_completion_batches_job() in scheduler.py, das
alle Batches abholt, deren Zeitfenster (BATCH_WINDOW_SECONDS) abgelaufen ist.

Rein prozessintern (kein DB-Table) - ClubHUB laeuft als einzelner
uvicorn-Worker (kein --workers-Flag, siehe Dockerfile), ein Neustart
verwirft dadurch hoechstens ein paar Sekunden alte, noch nicht verschickte
Batches. Fuer diesen rein informativen Anwendungsfall (keine sicherheits-
oder buchhaltungsrelevanten Daten - die Completion-Datensaetze selbst
landen unabhaengig davon sofort in der DB) unproblematisch, verglichen mit
der Komplexitaet einer persistenten Warteschlange."""
import threading

from . import ntptime

# Zeitfenster ab der ersten Erledigung in einem Bereich, bevor die
# gesammelte Benachrichtigung fuer diesen Bereich verschickt wird - im vom
# Auftrag vorgegebenen Bereich von 30-60 Sekunden.
BATCH_WINDOW_SECONDS = 45

_lock = threading.Lock()
# room_id -> {"room_name": str, "user_names": set[str], "count": int,
#             "first_seen": datetime, "group_ids": set[int]}
_pending = {}


def queue_task_completion(room, groups, user):
    """Reiht eine einzelne erledigte Aufgabe in den Sammel-Batch ihres
    Bereichs ein - verschickt selbst nichts. Ohne Zielgruppen (Aufgabe/
    Bereich ohne zugeordnete Gruppe) gibt es niemanden zu benachrichtigen,
    dann wird gar nicht erst eingereiht."""
    if not groups:
        return
    now = ntptime.now_utc()
    with _lock:
        entry = _pending.get(room.id)
        if entry is None:
            entry = {
                "room_name": room.name,
                "user_names": set(),
                "count": 0,
                "first_seen": now,
                "group_ids": set(),
            }
            _pending[room.id] = entry
        entry["count"] += 1
        entry["user_names"].add(user.name)
        entry["group_ids"].update(g.id for g in groups)


def pop_due_batches(now):
    """Gibt alle Batches zurueck, deren Zeitfenster abgelaufen ist, und
    entfernt sie aus der Warteschlange - fuer den periodischen Scheduler-Job
    (siehe check_completion_batches_job in scheduler.py). Jeder Eintrag wird
    dabei genau einmal zurueckgegeben, daher hoechstens eine Benachrichtigung
    pro Bereich und Durchgang."""
    due = []
    with _lock:
        for room_id, entry in list(_pending.items()):
            if (now - entry["first_seen"]).total_seconds() >= BATCH_WINDOW_SECONDS:
                due.append((room_id, entry))
                del _pending[room_id]
    return due
