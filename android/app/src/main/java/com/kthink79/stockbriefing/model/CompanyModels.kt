package com.kthink79.stockbriefing.model

data class QuarterlyEarnings(
    val quarter: String,
    val revenue: String,
    val operatingProfit: String,
    val comment: String
)

data class CompanyNews(
    val title: String,
    val source: String
)

data class CompanyValuationReport(
    val ticker: String,
    val name: String,
    val currency: String,
    val price: Double,
    val changePct: String,
    val marketCap: String,
    val per: Double,
    val pbr: Double,
    val eps: Double,
    val bps: Double,
    val dividendYield: String,
    val quarterlyEarnings: QuarterlyEarnings,
    val earningsNews: List<CompanyNews>,
    val geminiValuationSummary: String
)
