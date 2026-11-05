package com.kthink79.stockbriefing.model

data class TraceEvent(
    val timestamp: String,
    val title: String,
    val source: String,
    val contentSnippet: String,
    val impactLevel: String
)

data class TopicTrace(
    val id: String,
    val keywordOrTopic: String,
    val registeredAt: String,
    val events: List<TraceEvent>,
    val accumulatedSummary: String
)

data class SchedulePreference(
    val dailyReportTimes: List<String> = listOf("08:00"),
    val intervalEnabled: Boolean = false,
    val intervalMinutes: Int = 60,
    val marketHoursOnly: Boolean = true
)
