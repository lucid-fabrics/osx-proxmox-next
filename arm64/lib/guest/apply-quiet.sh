#!/bin/sh
# apply-quiet.sh: the quiet guest profile for any macOS guest golden. Run in the guest as root:  sh apply-quiet.sh [live]
# Persistently disables (launchd disabled databases, honoured from the next cold boot) 144 launchd labels in system and gui/501, turns the
# screensaver, display sleep and Spotlight indexing off. With `live` the labels are also booted out now (use it for a snapshot flow;
# it frees the CPU at once, drag +23 %, idle 5.3 -> 2.1 cores). Idempotent.
# KEEP (env, space separated labels) are never disabled and are enabled again. Default: com.apple.backgroundtaskmanagementd com.apple.kernelmanagerd.
# Finding: com.apple.kernelmanagerd must NOT be disabled either. With it disabled a cold boot has no working window drag (0-1 frames);
# with it kept the drag is 8.3-8.6 fps. kernelmanager_helper, fontworker, dasd, coreduetd and g7/g8 are fine disabled.
# Finding: com.apple.backgroundtaskmanagementd must NOT be disabled. With it disabled a cold boot comes up to the desktop but sshd resets
# every connection (kex_exchange_identification). This script enables it again if an older profile disabled it.
# Safe for a lab VM only: no iCloud, Siri, Spotlight, software update, backup, analytics, crash reporting, MDM, ML services, dasd,
# coreduetd, kernelmanagerd, fontworker, screensharing agent.
KEEP=${KEEP:-com.apple.backgroundtaskmanagementd com.apple.kernelmanagerd}
LIVE=0; [ "$1" = live ] && LIVE=1
n=0
while read l; do
  [ -z "$l" ] && continue
  case " $KEEP " in *" $l "*) continue;; esac
  for d in system gui/501; do
    launchctl disable $d/$l 2>/dev/null && n=$((n+1))
    [ $LIVE = 1 ] && launchctl bootout $d/$l 2>/dev/null
  done
done <<'LABELS'
com.apple.accessoryaccessd
com.apple.accessoryd
com.apple.accessorysensormgrd
com.apple.accessoryupdaterd
com.apple.analyticsagent
com.apple.analyticsd
com.apple.appleseed.seedusaged
com.apple.ap.promotedcontentd
com.apple.assistant_cdmd
com.apple.assistantd
com.apple.assistant_service
com.apple.audioanalyticsd
com.apple.backgroundtaskmanagementd
com.apple.backupd
com.apple.backupd-helper
com.apple.BiomeAgent
com.apple.biomed
com.apple.biomesyncd
com.apple.bird
com.apple.bridgeOSUpdateProxy
com.apple.callintelligenced
com.apple.cloudd
com.apple.cloudphotod
com.apple.CloudPhotosConfiguration
com.apple.cloudsettingssyncagent
com.apple.cloudtelemetryd
com.apple.cmio.iOSScreenCaptureAssistant
com.apple.cmio.registerassistantservice
com.apple.cmio.uvcassistantextension
com.apple.cmio.VDCAssistant
com.apple.commerce
com.apple.coreduetd
com.apple.corespotlightd
com.apple.corespotlightservice
com.apple.dasd
com.apple.devicemanagementclient.ManagedAppsAgent
com.apple.devicemanagementclient.managedappsd
com.apple.devicemanagementclient.managedeventsd
com.apple.diagnosticextensionsd
com.apple.diagnostics_agent
com.apple.diagnosticservicesd
com.apple.diagnosticspushd
com.apple.DiagnosticsReporter
com.apple.duetexpertd
com.apple.ecosystemanalyticsd
com.apple.findmy.findmybeaconingd
com.apple.findmy.findmylocateagent
com.apple.findmymacd
com.apple.findmymacmessenger
com.apple.fontworker
com.apple.gamed
com.apple.generativeexperiencesd
com.apple.geoanalyticsd
com.apple.geod
com.apple.homeenergyd
com.apple.icloud.findmydeviced
com.apple.icloud.findmydeviced.findmydevice-user-agent
com.apple.iCloudHelper
com.apple.icloudmailagent
com.apple.iCloudNotificationAgent
com.apple.icloud.searchpartyd
com.apple.icloud.searchpartyuseragent
com.apple.iCloudUserNotificationsd
com.apple.icloudwebd
com.apple.inputanalyticsd
com.apple.InstallerDiagnostics.installerdiagd
com.apple.InstallerDiagnostics.installerdiagwatcher
com.apple.intelligencecontextd
com.apple.intelligenceflowd
com.apple.intelligenceplatformd
com.apple.intelligencetasksd
com.apple.itunescloudd
com.apple.kernelmanagerd
com.apple.kernelmanager_helper
com.apple.knowledge-agent
com.apple.knowledgeconstructiond
com.apple.managedappdistributionagent
com.apple.managedappdistributiond
com.apple.ManagedClient
com.apple.ManagedClientAgent.agent
com.apple.ManagedClientAgent.enrollagent
com.apple.ManagedClient.enroll
com.apple.ManagedClient.mechanism
com.apple.managedcorespotlightd
com.apple.maps.destinationd
com.apple.Maps.mapspushd
com.apple.Maps.mapssyncd
com.apple.mdmclient.agent
com.apple.mdmclient.daemon
com.apple.mediaanalysisd
com.apple.metadata.mds
com.apple.metadata.mds.index
com.apple.metadata.mds.scan
com.apple.metadata.mds.spindump
com.apple.MobileAccessoryUpdater.fudHelperAgent
com.apple.MobileSoftwareUpdate.CleanupPreparePathService
com.apple.MobileSoftwareUpdate.CryptegraftService
com.apple.mobile.softwareupdated
com.apple.modelcatalogd
com.apple.modelmanagerd
com.apple.newsd
com.apple.osanalytics.osanalyticshelper
com.apple.parsecd
com.apple.parsec-fbf
com.apple.photoanalysisd
com.apple.photolibraryd
com.apple.privatecloudcomputed
com.apple.protectedcloudstorage.protectedcloudkeysyncing
com.apple.RemoteManagementAgent
com.apple.remotemanagementd
com.apple.ReportCrash
com.apple.ReportCrash.Root
com.apple.screensharing.agent
com.apple.screensharing.menuextra
com.apple.screensharing.MessagesAgent
com.apple.ScreenTimeAgent
com.apple.ScreenTimeSettingsAgent
com.apple.SecureBackupDaemon
com.apple.security.cloudkeychainproxy3
com.apple.siri.acousticsignature
com.apple.siriactionsd
com.apple.siriappintentsd
com.apple.siri.context.service
com.apple.siriinferenced
com.apple.siriknowledged
com.apple.sirittsd
com.apple.SiriTTSTrainingAgent
com.apple.softwareupdated
com.apple.SoftwareUpdateNotificationManager
com.apple.spotlightknowledged
com.apple.spotlightknowledged.importer
com.apple.spotlightknowledged.updater
com.apple.suggestd
com.apple.triald
com.apple.triald.system
com.apple.unmountassistant.sysagent
com.apple.unmountassistant.useragent
com.apple.UsageTrackingAgent
com.apple.visualintelligenced
com.apple.voicebankingd
com.apple.weatherd
com.apple.wifianalyticsd
com.apple.XProtect.agent.scan
com.apple.XProtect.agent.scan.startup
com.apple.XProtect.daemon.scan
com.apple.XProtect.daemon.scan.startup
LABELS
for k in $KEEP; do for d in system gui/501; do launchctl enable $d/$k 2>/dev/null; done; done
echo "disabled entries written: $n"
mdutil -a -i off 2>&1 | tail -3
U=${LABUSER:-snap}
sudo -u $U defaults -currentHost write com.apple.screensaver idleTime -int 0
sudo -u $U defaults write com.apple.screensaver idleTime -int 0
sudo -u $U defaults write com.apple.screensaver askForPassword -int 0
sudo -u $U defaults -currentHost write com.apple.screensaver CleanExit -string YES
pmset -a displaysleep 0 sleep 0 disksleep 0 powernap 0 2>/dev/null
launchctl print-disabled system | grep -c disabled
sync
