# HidrateSpark Cloud Sync

Home Assistant companion integration that uploads HidrateSpark bottle sips to HidrateSpark Cloud. Requires Home Assistant 2026.3+ and the [HA-Hidratespark BLE integration](https://github.com/bditter/HA-Hidratespark).

## Install with HACS

1. Install and configure HA-Hidratespark through HACS first. Confirm the bottle sensors and serial number are available.
2. HACS → menu → Custom repositories: add `https://github.com/HungryBug/Hidratespark_Cloud_Sync`, category **Integration**.
3. Download **HidrateSpark Cloud Sync** and restart Home Assistant.
4. Settings → Devices & services → Add integration → **HidrateSpark Cloud Sync**.
5. Select the same bottle's Last sip time and Last sip volume sensors. Enter its serial number, HidrateSpark credentials and IANA time zone. Add a separate entry for each bottle.

Update through HACS, then restart Home Assistant. No manual file copying or YAML credentials are required. HACS does not automatically download the BLE custom repository dependency.

## Behavior and diagnostics

- Separate persistent queue per bottle, serial uploads, stable sip IDs and retry backoff.
- Existing records become the initial baseline; queued records survive reloads/restarts.
- Cloud sync status, Pending uploads and Last successful sync sensors expose progress. Authentication failures request reauthentication.
- Permanent/uncertain failures remain stored and appear as `failed_uploads` on the status sensor. Use `hidratespark_cloud_sync.retry_failed` with the Cloud Sync config entry ID to retry/reconcile them.
- A timed-out write is queried by its stable clientSipId before any further action. If its outcome cannot be established, it is retained for review and never blindly reposted. The server has no verified exactly-once guarantee.

## Compatibility and limits

The source adapter reads paired sip records from bditter/HA-Hidratespark's existing dispatcher/ledger without modifying the BLE integration. Reviewed upstream commit: `dabbfca9a9624d36eddc4b573de21c9e8f7c974e`. Other forks or changed internals may be incompatible. The source retains at most 200 sip records, so records evicted while Cloud Sync is stopped cannot be recovered.

This is an unofficial integration. Credentials are configured locally in Home Assistant and are not bundled. Runtime files contain application-level Parse identifiers required by the cloud protocol, not personal account credentials. Cross-client duplicates (for example, the official app separately uploading the same sip) are not prevented. Offline unit tests passed; live Home Assistant/bottle/cloud end-to-end validation is still required.

## HACS 安装（中文）

先通过 HACS 安装并配置原 HA-Hidratespark，然后在 HACS 自定义存储库添加本仓库，类型选择“集成”。下载后重启 HA，再到“设置 → 设备与服务”添加 HidrateSpark Cloud Sync。每只水杯单独配置账号、序列号、对应实体及真实时区。

状态实体用于查看队列和失败信息。不确定是否已写入云端的记录不会盲目重发，需要人工核实。队列保存在 HA `.storage` 中，不受 HACS 代码更新覆盖。
