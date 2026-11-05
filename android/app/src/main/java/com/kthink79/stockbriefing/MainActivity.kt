package com.kthink79.stockbriefing

import android.os.Bundle
import androidx.activity.ComponentActivity
import com.kthink79.stockbriefing.notification.AlarmScheduler

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Schedule daily morning 8 AM notification
        AlarmScheduler.scheduleDaily8AMBriefing(this)
    }
}
