"""Constants for Home Hue Scene Monitor."""

from datetime import timedelta

DOMAIN = "home_hue_scene_monitor"

PLATFORMS = ["sensor"]

UPDATE_INTERVAL = timedelta(seconds=2)

MANAGED_ZONES = {
    "main_area": {
        "name": "Main Area",
        "group_id": "17ab93f4-24f5-4d8c-bbf1-ad0d5006b258",
    },
    "front_eve": {
        "name": "Front Eve",
        "group_id": "5f73b760-dd70-4131-ad03-5452c271d8e4",
    },
    "path": {
        "name": "Path",
        "group_id": "d1e5ec88-d6e5-4dc7-9bf6-1113be8d6bb9",
    },
    "backyard": {
        "name": "Backyard",
        "group_id": "873874ab-e1d2-473f-8430-2ef2fafb5a15",
    },
}
