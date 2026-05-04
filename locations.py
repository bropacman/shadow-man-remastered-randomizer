"""
Shadow Man Remastered - Location Definitions
All check locations, derived from levels.txt analysis.

Location IDs are: LOCATION_BASE_ID + sequential index
"""

from BaseClasses import Location

LOCATION_BASE_ID = 770_200  # Offset from item IDs


class ShadowManLocation(Location):
    game = "Shadow Man Remastered"


# ─── Location Table ────────────────────────────────────────────────────────────
# Format: "Location Name" -> {"id": int, "region": str, "type": str}
# Requirements are handled in rules.py, not here.

_loc_id = LOCATION_BASE_ID

def _loc(region: str, loc_type: str) -> dict:
    global _loc_id
    data = {"id": _loc_id, "region": region, "type": loc_type}
    _loc_id += 1
    return data


location_table: dict[str, dict] = {

    # ── Level 0: Louisiana Swampland ──────────────────────────────────────────
    "Swampland - Dark Soul 1":      _loc("Louisiana Swampland", "dark_soul"),
    "Swampland - Dark Soul 2":      _loc("Louisiana Swampland", "dark_soul"),
    "Swampland - Dark Soul 3":      _loc("Louisiana Swampland", "dark_soul"),
    "Swampland - Dark Soul 4":      _loc("Louisiana Swampland", "dark_soul"),
    "Swampland - Dark Soul 5":      _loc("Louisiana Swampland", "dark_soul"),
    "Swampland - Dark Soul 6":      _loc("Louisiana Swampland", "dark_soul"),
    "Swampland - Dark Soul 7":      _loc("Louisiana Swampland", "dark_soul"),
    "Swampland - Dark Soul 8":      _loc("Louisiana Swampland", "dark_soul"),
    "Swampland - Secret 13":        _loc("Louisiana Swampland", "secret"),

    # ── Level 1: New York Tenement ────────────────────────────────────────────
    "New York - Dark Soul 9":       _loc("New York Tenement", "dark_soul"),
    "New York - Avery Boss Check":  _loc("New York Tenement", "key_item"),
    "New York - Secret 4":          _loc("New York Tenement", "secret"),

    # ── Level 2: Texas Prison ─────────────────────────────────────────────────
    "Texas - Dark Soul 10":         _loc("Texas Prison", "dark_soul"),
    "Texas - Batty Boss Check":     _loc("Texas Prison", "key_item"),
    "Texas - Secret 6":             _loc("Texas Prison", "secret"),

    # ── Level 3: London Underground ───────────────────────────────────────────
    "London - Dark Soul 11":        _loc("London Underground", "dark_soul"),
    "London - Jack Boss Check":     _loc("London Underground", "key_item"),
    "London - Secret 3":            _loc("London Underground", "secret"),

    # ── Level 4: Florida Summer Camp ──────────────────────────────────────────
    "Florida - Dark Soul 12":       _loc("Florida Summer Camp", "dark_soul"),
    "Florida - Milton Boss Check":  _loc("Florida Summer Camp", "key_item"),
    "Florida - Secret 16":          _loc("Florida Summer Camp", "secret"),

    # ── Level 5: Mojave Desert ────────────────────────────────────────────────
    "Mojave - Dark Soul 13":        _loc("Mojave Desert Salvage Yard", "dark_soul"),
    "Mojave - Marco Boss Check":    _loc("Mojave Desert Salvage Yard", "key_item"),
    "Mojave - Secret 1":            _loc("Mojave Desert Salvage Yard", "secret"),
    "Mojave - Secret 9":            _loc("Mojave Desert Salvage Yard", "secret"),

    # ── Level 6: Marrow Gates (Deadside Hub) ──────────────────────────────────
    "Marrow Gates - Path of Shadows":   _loc("Marrow Gates", "key_item"),
    "Marrow Gates - Book of Shadows":   _loc("Marrow Gates", "key_item"),
    "Marrow Gates - Dark Soul 14":      _loc("Marrow Gates", "dark_soul"),
    "Marrow Gates - Dark Soul 15":      _loc("Marrow Gates", "dark_soul"),
    "Marrow Gates - Dark Soul 16":      _loc("Marrow Gates", "dark_soul"),
    "Marrow Gates - Dark Soul 17":      _loc("Marrow Gates", "dark_soul"),
    "Marrow Gates - Secret 18":         _loc("Marrow Gates", "secret"),

    # ── Level 7: Deadside Wasteland ───────────────────────────────────────────
    "Wasteland - Dark Soul 18":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Dark Soul 19":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Dark Soul 20":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Dark Soul 21":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Dark Soul 22":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Dark Soul 23":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Dark Soul 24":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Dark Soul 25":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Dark Soul 26":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Dark Soul 27":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Dark Soul 28":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Dark Soul 29":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Dark Soul 30":     _loc("Deadside Wasteland", "dark_soul"),
    "Wasteland - Asson":            _loc("Deadside Wasteland", "key_item"),
    "Wasteland - Baton":            _loc("Deadside Wasteland", "key_item"),
    "Wasteland - Secret 11":        _loc("Deadside Wasteland", "secret"),

    # ── Level 8: Asylum Gateway ───────────────────────────────────────────────
    "Gateway - Engineers Key":      _loc("Asylum Gateway", "key_item"),
    "Gateway - Dark Soul 32":       _loc("Asylum Gateway", "dark_soul"),
    "Gateway - Dark Soul 37":       _loc("Asylum Gateway", "dark_soul"),
    "Gateway - Dark Soul 39":       _loc("Asylum Gateway", "dark_soul"),
    "Gateway - Dark Soul 54":       _loc("Asylum Gateway", "dark_soul"),
    "Gateway - Dark Soul 55":       _loc("Asylum Gateway", "dark_soul"),
    "Gateway - Secret 7":           _loc("Asylum Gateway", "secret"),

    # ── Level 9: Experimentation Rooms ────────────────────────────────────────
    "ExpeRooms - Dark Soul 33":     _loc("Experimentation Rooms", "dark_soul"),
    "ExpeRooms - Dark Soul 34":     _loc("Experimentation Rooms", "dark_soul"),
    "ExpeRooms - Dark Soul 35":     _loc("Experimentation Rooms", "dark_soul"),
    "ExpeRooms - Dark Soul 36":     _loc("Experimentation Rooms", "dark_soul"),
    "ExpeRooms - Tete de Mort":     _loc("Experimentation Rooms", "key_item"),
    "ExpeRooms - Retractor":        _loc("Experimentation Rooms", "key_item"),
    "ExpeRooms - Violator 2":       _loc("Experimentation Rooms", "key_item"),
    "ExpeRooms - Secret 19":        _loc("Experimentation Rooms", "secret"),

    # ── Level 10: Schism Chambers ─────────────────────────────────────────────
    "Schism - Dark Soul 31":        _loc("Schism Chambers", "dark_soul"),
    "Schism - Secret 0":            _loc("Schism Chambers", "secret"),

    # ── Level 11: The Dark Engine (Legion Boss) ───────────────────────────────
    "Dark Engine - Dark Soul 38":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 40":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 41":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 42":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 43":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 44":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 45":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 46":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 47":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 48":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 49":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 50":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 51":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 52":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Dark Soul 53":   _loc("Dark Engine", "dark_soul"),
    "Dark Engine - Secret 20":      _loc("Dark Engine", "secret"),
    "Defeat Legion":                _loc("Dark Engine", "goal"),

    # ── Level 12: Touch Gad Temple ────────────────────────────────────────────
    "TouchGad Temple - Touch Gad":      _loc("Touch Gad Temple", "key_item"),
    "TouchGad Temple - Poigne":         _loc("Touch Gad Temple", "key_item"),
    "TouchGad Temple - Dark Soul 56":   _loc("Touch Gad Temple", "dark_soul"),
    "TouchGad Temple - Dark Soul 57":   _loc("Touch Gad Temple", "dark_soul"),
    "TouchGad Temple - Dark Soul 58":   _loc("Touch Gad Temple", "dark_soul"),
    "TouchGad Temple - Dark Soul 59":   _loc("Touch Gad Temple", "dark_soul"),
    "TouchGad Temple - Dark Soul 60":   _loc("Touch Gad Temple", "dark_soul"),
    "TouchGad Temple - Dark Soul 61":   _loc("Touch Gad Temple", "dark_soul"),
    "TouchGad Temple - Dark Soul 62":   _loc("Touch Gad Temple", "dark_soul"),
    "TouchGad Temple - Dark Soul 63":   _loc("Touch Gad Temple", "dark_soul"),
    "TouchGad Temple - Dark Soul 64":   _loc("Touch Gad Temple", "dark_soul"),
    "TouchGad Temple - Dark Soul 65":   _loc("Touch Gad Temple", "dark_soul"),
    "TouchGad Temple - Dark Soul 66":   _loc("Touch Gad Temple", "dark_soul"),
    "TouchGad Temple - Secret 2":       _loc("Touch Gad Temple", "secret"),

    # ── Level 13: The Cageways ────────────────────────────────────────────────
    "Cageways - Retractor":         _loc("Cageways", "key_item"),
    "Cageways - Dark Soul 67":      _loc("Cageways", "dark_soul"),
    "Cageways - Dark Soul 68":      _loc("Cageways", "dark_soul"),
    "Cageways - Dark Soul 69":      _loc("Cageways", "dark_soul"),
    "Cageways - Dark Soul 70":      _loc("Cageways", "dark_soul"),
    "Cageways - Dark Soul 71":      _loc("Cageways", "dark_soul"),
    "Cageways - Dark Soul 72":      _loc("Cageways", "dark_soul"),
    "Cageways - Dark Soul 73":      _loc("Cageways", "dark_soul"),
    "Cageways - Dark Soul 74":      _loc("Cageways", "dark_soul"),
    "Cageways - Dark Soul 75":      _loc("Cageways", "dark_soul"),
    "Cageways - Dark Soul 76":      _loc("Cageways", "dark_soul"),
    "Cageways - Dark Soul 77":      _loc("Cageways", "dark_soul"),
    "Cageways - Secret 15":         _loc("Cageways", "secret"),

    # ── Level 14: The Playrooms ───────────────────────────────────────────────
    "Playrooms - Retractor":        _loc("Playrooms", "key_item"),
    "Playrooms - Violator":         _loc("Playrooms", "key_item"),
    "Playrooms - Dark Soul 78":     _loc("Playrooms", "dark_soul"),
    "Playrooms - Dark Soul 79":     _loc("Playrooms", "dark_soul"),
    "Playrooms - Dark Soul 80":     _loc("Playrooms", "dark_soul"),
    "Playrooms - Dark Soul 81":     _loc("Playrooms", "dark_soul"),
    "Playrooms - Dark Soul 82":     _loc("Playrooms", "dark_soul"),
    "Playrooms - Dark Soul 83":     _loc("Playrooms", "dark_soul"),
    "Playrooms - Dark Soul 84":     _loc("Playrooms", "dark_soul"),
    "Playrooms - Dark Soul 85":     _loc("Playrooms", "dark_soul"),
    "Playrooms - Dark Soul 86":     _loc("Playrooms", "dark_soul"),
    "Playrooms - Dark Soul 87":     _loc("Playrooms", "dark_soul"),
    "Playrooms - Secret 8":         _loc("Playrooms", "secret"),

    # ── Level 15: Walk Gad Temple ─────────────────────────────────────────────
    "WalkGad Temple - Walk Gad":        _loc("Walk Gad Temple", "key_item"),
    "WalkGad Temple - Dark Soul 88":    _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Dark Soul 89":    _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Dark Soul 90":    _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Dark Soul 91":    _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Dark Soul 92":    _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Dark Soul 93":    _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Dark Soul 94":    _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Dark Soul 95":    _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Dark Soul 96":    _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Dark Soul 97":    _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Dark Soul 98":    _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Dark Soul 99":    _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Dark Soul 100":   _loc("Walk Gad Temple", "dark_soul"),
    "WalkGad Temple - Secret 10":       _loc("Walk Gad Temple", "secret"),

    # ── Level 16: The Lavaducts ───────────────────────────────────────────────
    "Lavaducts - Retractor":        _loc("Lavaducts", "key_item"),
    "Lavaducts - Dark Soul 101":    _loc("Lavaducts", "dark_soul"),
    "Lavaducts - Dark Soul 102":    _loc("Lavaducts", "dark_soul"),
    "Lavaducts - Dark Soul 103":    _loc("Lavaducts", "dark_soul"),
    "Lavaducts - Dark Soul 104":    _loc("Lavaducts", "dark_soul"),
    "Lavaducts - Dark Soul 105":    _loc("Lavaducts", "dark_soul"),
    "Lavaducts - Dark Soul 106":    _loc("Lavaducts", "dark_soul"),
    "Lavaducts - Dark Soul 107":    _loc("Lavaducts", "dark_soul"),
    "Lavaducts - Dark Soul 108":    _loc("Lavaducts", "dark_soul"),
    "Lavaducts - Dark Soul 109":    _loc("Lavaducts", "dark_soul"),
    "Lavaducts - Secret 21":        _loc("Lavaducts", "secret"),

    # ── Level 17: Swim Gad Temple ─────────────────────────────────────────────
    "SwimGad Temple - Swim Gad":        _loc("Swim Gad Temple", "key_item"),
    "SwimGad Temple - Calabash":        _loc("Swim Gad Temple", "key_item"),
    "SwimGad Temple - Dark Soul 110":   _loc("Swim Gad Temple", "dark_soul"),
    "SwimGad Temple - Dark Soul 111":   _loc("Swim Gad Temple", "dark_soul"),
    "SwimGad Temple - Dark Soul 112":   _loc("Swim Gad Temple", "dark_soul"),
    "SwimGad Temple - Dark Soul 113":   _loc("Swim Gad Temple", "dark_soul"),
    "SwimGad Temple - Dark Soul 114":   _loc("Swim Gad Temple", "dark_soul"),
    "SwimGad Temple - Dark Soul 115":   _loc("Swim Gad Temple", "dark_soul"),
    "SwimGad Temple - Dark Soul 116":   _loc("Swim Gad Temple", "dark_soul"),
    "SwimGad Temple - Dark Soul 117":   _loc("Swim Gad Temple", "dark_soul"),
    "SwimGad Temple - Secret 12":       _loc("Swim Gad Temple", "secret"),

    # ── Level 18: The Fogometers ──────────────────────────────────────────────
    "Fogometers - Light Soul":      _loc("Fogometers", "key_item"),
    "Fogometers - Dark Soul 118":   _loc("Fogometers", "dark_soul"),
    "Fogometers - Dark Soul 119":   _loc("Fogometers", "dark_soul"),
    "Fogometers - Dark Soul 120":   _loc("Fogometers", "dark_soul"),
    "Fogometers - Secret 5":        _loc("Fogometers", "secret"),
}
