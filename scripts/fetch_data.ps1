param(
    [string]$Dest = "data/StarRailData"
)

$ErrorActionPreference = "Stop"
$Repo = "https://github.com/DimbreathBot/TurnBasedGameData.git"
$SparsePatterns = @(
    "/Story",
    "/TextMap",
    # 整个 Mission 目录（含 TrainVisitor）：主线/支线演出脚本，对白集中在 Act*.json
    "/Config/Level/Mission",
    "/ExcelOutput/TalkSentenceConfig.json",
    "/ExcelOutput/MessageContactsCamp.json",
    "/ExcelOutput/MessageContactsConfig.json",
    "/ExcelOutput/MessageItemConfig.json",
    "/ExcelOutput/MessageGroupConfig.json",
    "/ExcelOutput/MessageSectionConfig.json",
    "/ExcelOutput/BookSeriesConfig.json",
    "/ExcelOutput/AvatarConfig.json",
    "/ExcelOutput/AvatarRankConfig.json",
    "/ExcelOutput/AvatarSkillConfig.json",
    "/ExcelOutput/MainMission.json",
    "/ExcelOutput/NounAtlas.json",
    "/ExcelOutput/PerformanceSkipOverride.json",
    "/ExcelOutput/MappingInfo.json",
    "/ExcelOutput/LocalbookConfig.json",
    "/ExcelOutput/ChronicleConclusion.json",
    "/ExcelOutput/LoadingDesc.json",
    "/ExcelOutput/MonsterAtlasExtraPhase.json",
    "/ExcelOutput/MonsterAtlasExtraPhases.json",
    "/ExcelOutput/SubMission.json",
    "/ExcelOutput/ItemConfig*.json",
    "/ExcelOutput/ItemPurpose.json",
    "/ExcelOutput/MonsterConfig.json",
    "/ExcelOutput/MonsterTemplateConfig.json",
    "/ExcelOutput/MonsterSkillConfig.json",
    "/ExcelOutput/VoiceAtlas.json",
    "/ExcelOutput/StoryAtlas.json"
)

if (Test-Path $Dest) {
    Write-Host "Target $Dest already exists; skip clone, ensuring extra ExcelOutput files."
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null
    git clone --depth 1 --filter=blob:none --sparse $Repo $Dest
    if ($LASTEXITCODE -ne 0) { throw "clone failed" }
    git -C $Dest sparse-checkout set --no-cone $SparsePatterns
    if ($LASTEXITCODE -ne 0) { throw "sparse-checkout failed" }
}

# Fallback: if github.com is unreachable (partial clone cannot fetch new blobs),
# download any missing ExcelOutput files via raw.githubusercontent.com.
$RawBase = "https://raw.githubusercontent.com/DimbreathBot/TurnBasedGameData/main"
$NeedFiles = @(
    "TalkSentenceConfig.json",
    "MessageContactsCamp.json",
    "MessageContactsConfig.json",
    "MessageItemConfig.json",
    "MessageGroupConfig.json",
    "MessageSectionConfig.json",
    "BookSeriesConfig.json",
    "AvatarConfig.json",
    "AvatarRankConfig.json",
    "AvatarSkillConfig.json",
    "MainMission.json",
    "NounAtlas.json",
    "PerformanceSkipOverride.json",
    "MappingInfo.json",
    "LocalbookConfig.json",
    "ChronicleConclusion.json",
    "LoadingDesc.json",
    "MonsterAtlasExtraPhase.json",
    "MonsterAtlasExtraPhases.json",
    "SubMission.json",
    "ItemPurpose.json",
    "MonsterConfig.json",
    "MonsterTemplateConfig.json",
    "MonsterSkillConfig.json",
    "VoiceAtlas.json",
    "StoryAtlas.json",
    "ItemConfig.json",
    "ItemConfigAvatar.json",
    "ItemConfigAvatarLD.json",
    "ItemConfigAvatarPlayerIcLD.json",
    "ItemConfigAvatarPlayerIcon.json",
    "ItemConfigAvatarRank.json",
    "ItemConfigAvatarRankLD.json",
    "ItemConfigAvatarSkin.json",
    "ItemConfigAvatarTest.json",
    "ItemConfigAvatarTestRank.json",
    "ItemConfigBook.json",
    "ItemConfigDisk.json",
    "ItemConfigEquipment.json",
    "ItemConfigLD.json",
    "ItemConfigPlayerRoomDynamic.json",
    "ItemConfigRelic.json",
    "ItemConfigTrainDynamic.json"
)
$ExcelDir = Join-Path $Dest "ExcelOutput"
$Downloaded = 0
foreach ($name in $NeedFiles) {
    $target = Join-Path $ExcelDir $name
    if (-not (Test-Path $target)) {
        Invoke-WebRequest -Uri "$RawBase/ExcelOutput/$name" -OutFile $target -TimeoutSec 120 -UseBasicParsing
        $Downloaded++
    }
}
if ($Downloaded -gt 0) {
    Write-Host "Downloaded $Downloaded missing ExcelOutput files via raw.githubusercontent.com"
}

Write-Host "Sparse checkout done. Files:"
Get-ChildItem -Recurse -File $Dest | Measure-Object | Select-Object -ExpandProperty Count
