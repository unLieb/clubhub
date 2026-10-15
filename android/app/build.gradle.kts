plugins {
    id("com.android.application")
}

android {
    namespace = "de.clubhub.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "de.clubhub.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
}
