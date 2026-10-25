# Automated 5-Minute Keep-Alive Creator via cron-job.org REST API
$ErrorActionPreference = "Stop"

Write-Host "`n=======================================================" -ForegroundColor Cyan
Write-Host "  cron-job.org — Automated 5-Min Keep-Alive Provisioner" -ForegroundColor Cyan
Write-Host "=======================================================`n" -ForegroundColor Cyan
Write-Host "Get your API Key from: https://console.cron-job.org/settings (under API Keys -> Create API Key)`n" -ForegroundColor Yellow

$apiKeySecure = Read-Host "Enter your cron-job.org API Key (typing hidden)" -AsSecureString
$apiKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($apiKeySecure)).Trim()

$headers = @{
    "Authorization" = "Bearer $apiKey"
    "Content-Type"  = "application/json"
}

$body = @{
    job = @{
        url             = "https://hallucination-detector-tveu.onrender.com"
        title           = "Hallucination Detector Keep-Alive (5m)"
        enabled         = $true
        saveResponses   = $false
        requestTimeout  = 30
        schedule        = @{
            timezone = "Asia/Kolkata"
            expiresAt = 0
            hours    = @(-1)
            mdays    = @(-1)
            minutes  = @(0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55)
            months   = @(-1)
            wdays    = @(-1)
        }
    }
} | ConvertTo-Json -Depth 5

$response = Invoke-RestMethod -Uri "https://api.cron-job.org/jobs" -Method Put -Headers $headers -Body $body
Write-Host "`n✅ Created 5-minute Keep-Alive cron job on cron-job.org! (Job ID: $($response.jobId))" -ForegroundColor Green
Write-Host "Target: https://hallucination-detector-tveu.onrender.com (Every 5 minutes)`n" -ForegroundColor Green
