package com.kthink79.stockbriefing.model

data class StockReport(
    val id: String,
    val date: String,
    val title: String,
    val summary: String,
    val keyPoints: List<String>,
    val indices: Map<String, String>,
    val semiconductorNews: List<String>,
    val aerospaceNews: List<String>
)
