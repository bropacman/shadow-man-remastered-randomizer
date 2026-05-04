class Region:
    def __init__(self, name, player, multiworld):
        self.name = name
        self.player = player
        self.multiworld = multiworld
        self.locations = []
        self.entrances = []

    def connect(self, region, rule=None):
        entrance = Entrance(self, region, rule)
        region.entrances.append(entrance)

class Entrance:
    def __init__(self, source, target, rule=None):
        self.source = source
        self.target = target
        self.access_rule = rule if rule else (lambda state: True)

class Location:
    def __init__(self, player, name, address=None, parent=None):
        self.name = name
        self.player = player
        self.address = address
        self.parent_region = parent
        self.progress_type = 0 # Standard

class MultiWorld:
    def __init__(self):
        self.regions = []

class LocationProgressType:
    Standard = 0
    Required = 1
    Excluded = 2