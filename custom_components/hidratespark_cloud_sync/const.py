"""Integration constants."""
DOMAIN = "hidratespark_cloud_sync"
SOURCE_DOMAIN = "ha_hidratespark"
PLATFORMS = ["sensor"]
BRIDGE_KEY = "apple_health_bridge"
BRIDGE_STORE_KEY = f"{DOMAIN}.apple_health_bridge"
BRIDGE_SIGNAL = f"{DOMAIN}_apple_health_bridge"
EVENT_PENDING = f"{DOMAIN}_apple_health_pending"
EVENT_BATCH_READY = f"{DOMAIN}_apple_health_batch_ready"
