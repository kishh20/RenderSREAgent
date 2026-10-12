# Interactive Secret Setup for RenderSREAgent (Keeps API keys out of chat logs)
$ErrorActionPreference = "Stop"
$Repo = "kishh20/RenderSREAgent"
$EnvFile = Join-Path $PSScriptRoot ".env"

Write-Host "`n=======================================================" -ForegroundColor Cyan
Write-Host "  RenderSREAgent — Secure GitHub Secrets Configurator" -ForegroundColor Cyan
Write-Host "=======================================================`n" -ForegroundColor Cyan

$defaultUrl = "https://hallucination-detector.onrender.com/_stcore/health"
$targetUrl = Read-Host "1. Enter your Render Health URL (Press Enter for default: $defaultUrl)"
if ([string]::IsNullOrWhiteSpace($targetUrl)) { $targetUrl = $defaultUrl }

$serviceId = Read-Host "2. Enter your RENDER_SERVICE_ID (e.g. srv-xxxxxxxxxxxx from Render dashboard URL)"

$renderKeySecure = Read-Host "3. Enter your RENDER_API_KEY (rnd_... — typing hidden)" -AsSecureString
$renderKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($renderKeySecure))

$tgTokenSecure = Read-Host "4. Enter your TELEGRAM_BOT_TOKEN (from @BotFather — typing hidden)" -AsSecureString
$tgToken = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($tgTokenSecure))

$tgChatId = Read-Host "5. Enter your TELEGRAM_CHAT_ID (from @userinfobot)"

$geminiKey = $env:GEMINI_API_KEY
if ([string]::IsNullOrWhiteSpace($geminiKey)) {
    $geminiSecure = Read-Host "6. Enter your GEMINI_API_KEY (AIza... — typing hidden)" -AsSecureString
    $geminiKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($geminiSecure))
}

Write-Host "`nUploading secrets to GitHub repository $Repo..." -ForegroundColor Yellow

gh secret set TARGET_URL --repo $Repo --body $targetUrl
if (-not [string]::IsNullOrWhiteSpace($serviceId)) { gh secret set RENDER_SERVICE_ID --repo $Repo --body $serviceId }
if (-not [string]::IsNullOrWhiteSpace($renderKey)) { gh secret set RENDER_API_KEY --repo $Repo --body $renderKey }
if (-not [string]::IsNullOrWhiteSpace($tgToken))   { gh secret set TELEGRAM_BOT_TOKEN --repo $Repo --body $tgToken }
if (-not [string]::IsNullOrWhiteSpace($tgChatId))  { gh secret set TELEGRAM_CHAT_ID --repo $Repo --body $tgChatId }
if (-not [string]::IsNullOrWhiteSpace($geminiKey)) { gh secret set GEMINI_API_KEY --repo $Repo --body $geminiKey }

# Save local gitignored .env for local testing
@"
APP_NAME=Hallucination Detector
TARGET_URL=$targetUrl
EXPECTED_KEYWORD=ok
RENDER_SERVICE_ID=$serviceId
RENDER_API_KEY=$renderKey
TELEGRAM_BOT_TOKEN=$tgToken
TELEGRAM_CHAT_ID=$tgChatId
GEMINI_API_KEY=$geminiKey
"@ | Set-Content -Path $EnvFile -Encoding UTF8

Write-Host "`nAll secrets saved and uploaded to GitHub Actions!" -ForegroundColor Green
Write-Host "Triggering a live GitHub Actions monitor run..." -ForegroundColor Cyan
gh workflow run monitor.yml --repo $Repo
Write-Host "Done! View live run at: https://github.com/$Repo/actions`n" -ForegroundColor Green
