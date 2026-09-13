# HidrateSpark Cloud Sync

<p align="center">
  <img src="custom_components/hidratespark_cloud_sync/brand/icon@2x.png" alt="HidrateSpark Cloud Sync" width="180">
</p>

Home Assistant companion integration that uploads HidrateSpark bottle sips to HidrateSpark Cloud. Requires Home Assistant 2026.3+ and the [HA-Hidratespark BLE integration](https://github.com/bditter/HA-Hidratespark).

## Install with HACS

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=HungryBug&repository=Hidratespark_Cloud_Sync&category=integration)

1. Install and configure HA-Hidratespark through HACS first. Confirm the bottle sensors and serial number are available.
2. HACS → menu → Custom repositories: add `https://github.com/HungryBug/Hidratespark_Cloud_Sync`, category **Integration**.
3. Download **HidrateSpark Cloud Sync** and restart Home Assistant.
4. Settings → Devices & services → Add integration → **HidrateSpark Cloud Sync**.
5. Select the same bottle's Last sip time, Last sip volume and Serial number sensors. Enter HidrateSpark credentials and an IANA time zone. Add a separate entry for each bottle.

The setup form also shows the shared Apple Health Bridge token. Copy it into your iPhone Shortcut. To view, change, disable, or regenerate it later, open the integration's **Reconfigure** flow. Regeneration requires confirmation and invalidates the old token immediately.

Update through HACS, then restart Home Assistant. No manual file copying or YAML credentials are required. HACS does not automatically download the BLE custom repository dependency.

## Behavior and diagnostics

- Separate persistent queue per bottle, serial uploads, stable sip IDs and retry backoff.
- Existing records become the initial baseline; queued records survive reloads/restarts.
- Cloud sync status, Pending uploads and Last successful sync sensors expose progress. Authentication failures request reauthentication.
- Permanent/uncertain failures remain stored and appear as `failed_uploads` on the status sensor. Use `hidratespark_cloud_sync.retry_failed` with the Cloud Sync config entry ID to retry/reconcile them.
- A timed-out write is queried by its stable clientSipId before any further action. If its outcome cannot be established, it is retained for review and never blindly reposted. The server has no verified exactly-once guarantee.

## Apple Health Bridge

Every captured sip is also saved in one persistent Apple Health queue shared by all configured bottles. Cloud failures do not block this queue. Pending records are never removed automatically; acknowledged records are kept for up to 180 days or 5,000 records. The bridge provides an Apple Health Bridge device with status, pending count, last successful sync, synced today, and oldest pending diagnostic sensors.

Configure an iPhone Shortcut to call these endpoints with the HTTP header `Authorization: Bearer YOUR_TOKEN`:

- `GET /api/hidratespark_cloud_sync/v1/apple_health/pending?limit=100`
- `POST /api/hidratespark_cloud_sync/v1/apple_health/ack` with `{"id":"EVENT_ID"}` or `{"ids":["EVENT_ID"]}`
- `POST /api/hidratespark_cloud_sync/v1/apple_health/fail` with `{"id":"EVENT_ID","error":"MESSAGE"}`
- `GET /api/hidratespark_cloud_sync/v1/apple_health/status`
- `GET /api/hidratespark_cloud_sync/v1/apple_health/history?status=all&limit=100`

The Shortcut should fetch pending records oldest first, check its own event-ID ledger before each HealthKit write, add `volume_ml` as Dietary Water only when the ID is new, and ACK the ID afterward. Delivery is at least once; the local pre-write check prevents a retry after a lost ACK from writing the same sample twice. ACK is idempotent. The `hidratespark_cloud_sync.requeue_apple_health` action can replay acknowledged records by date and bottle, so use it only when the Shortcut performs this ID check.

Use HTTPS through a trusted private connection. WireGuard or another VPN is recommended. Do not expose an unencrypted Home Assistant port 8123 to the public internet. The token is accepted only in the Bearer header and is never exposed through sensors, status/history responses, events, logs, or diagnostics.

## Compatibility and limits

The source adapter reads paired sip records from bditter/HA-Hidratespark's existing dispatcher/ledger without modifying the BLE integration. Reviewed upstream commit: `dabbfca9a9624d36eddc4b573de21c9e8f7c974e`. Other forks or changed internals may be incompatible. The source retains at most 200 sip records, so records evicted while Cloud Sync is stopped cannot be recovered.

This is an unofficial integration. Credentials are configured locally in Home Assistant and are not bundled. Runtime files contain application-level Parse identifiers required by the cloud protocol, not personal account credentials. Cross-client duplicates (for example, the official app separately uploading the same sip) are not prevented. Offline unit tests passed; live Home Assistant/bottle/cloud end-to-end validation is still required.

## HACS 安装（中文）

先通过 HACS 安装并配置原 HA-Hidratespark，然后在 HACS 自定义存储库添加本仓库，类型选择“集成”。下载后重启 HA，再到“设置 → 设备与服务”添加 HidrateSpark Cloud Sync。请选择同一只水杯的 Last sip time、Last sip volume 和 Serial number 实体；每只水杯单独配置账号和真实时区。首次配置会显示所有水杯共用的 Apple 健康桥接令牌，请复制到快捷指令；之后可通过“重新配置”查看、修改或重新生成。

状态实体用于查看队列和失败信息。不确定是否已写入云端的记录不会盲目重发，需要人工核实。队列保存在 HA `.storage` 中，不受 HACS 代码更新覆盖。

Apple 健康桥接采用至少一次投递。快捷指令写入 HealthKit 前必须按事件 ID 去重，成功后再 ACK；重复 ACK 安全。请通过 WireGuard 等 VPN 使用 HTTPS，不要把未加密的 8123 端口暴露到公网。
