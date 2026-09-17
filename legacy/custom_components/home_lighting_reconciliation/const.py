"""Control boundaries; colours and priorities remain in the package."""

DOMAIN = "home_lighting_reconciliation"
SIGNAL = DOMAIN + "_updated"
SURFACES = ("main_area", "front_eve", "path", "backyard")
TRANSIENTS = {
    "automation.49ers_live_game_lighting_and_score_celebration": ("main_area", "front_eve"),
    "automation.pool_and_hot_tub_ready_light_alert": ("main_area",),
    "automation.powerwall_flash_house_lights_on_grid_failure": ("main_area",),
    "automation.spa_gauge_session_manager": ("backyard",),
    "automation.liquor_cabinet_display_lighting": ("main_area",),
}
EVALUATORS = {
    "script.home_lighting_evaluate_and_apply": ("main_area", "front_eve"),
    "script.home_lighting_apply_path_state": ("path",),
    "script.home_lighting_evaluate_backyard": ("backyard",),
    "automation.home_lighting_daily_schedule": SURFACES,
}
CONTROL = {
    *(f"input_boolean.home_lighting_{s}_window" for s in SURFACES),
    *(f"input_boolean.home_lighting_manual_{s}" for s in SURFACES),
    "input_boolean.home_lighting_manual_kitchen_left_cabinet",
    "input_boolean.home_lighting_manual_kitchen_right_cabinet",
    "input_boolean.home_lighting_manual_living_left_cabinets",
    "input_boolean.home_lighting_manual_living_right_cabinets",
    "input_boolean.home_lighting_manual_living_left_ceiling",
    "input_boolean.home_lighting_manual_living_right_ceiling",
    "input_boolean.home_lighting_manual_liquor_cabinet",
    "input_boolean.home_lighting_holiday_active",
    "input_text.home_lighting_holiday_key",
    "input_boolean.spa_gauge_active",
    "binary_sensor.hue_bridge_living_room",
    "binary_sensor.hue_bridge_backyard",
    "binary_sensor.liquor_cabinet_door_r",
    "binary_sensor.liquor_cabinet_l_door",
    "sensor.nfl_san_francisco_49ers",
    *(f"sensor.{s}_last_recall" for s in ("main_area", "front_eve", "path", "backyard")),
}
