from datetime import datetime, timezone
import zoneinfo

mountain_dt = datetime.now(zoneinfo.ZoneInfo("America/Denver"))
print(f"Mountain dt is {mountain_dt}")

eastern_dt = mountain_dt.astimezone(zoneinfo.ZoneInfo("America/New_York"))
print(f"Eastern dt is {eastern_dt}")

eastern_dt_redux = eastern_dt.astimezone(zoneinfo.ZoneInfo("America/New_York"))
print(f"Eastern dt redux is {eastern_dt_redux}")

print(f"Local TZ is {eastern_dt.astimezone()}")
