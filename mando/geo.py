import math

_R = 6371008.8
_CARDINALS = ("N", "NE", "E", "SE", "S", "SO", "O", "NO")


def haversine_m(lat1, lon1, lat2, lon2):
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * _R * math.asin(math.sqrt(min(1.0, a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlam)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def cardinal_es(deg):
    return _CARDINALS[int((deg % 360.0 + 22.5) // 45.0) % 8]


def point_in_ring(lat, lon, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            if lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def _seg_dist(px, py, ax, ay, bx, by):
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom == 0.0:
        return math.hypot(px - ax, py - ay)
    t = min(1.0, max(0.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_to_ring_m(lat, lon, ring):
    if point_in_ring(lat, lon, ring):
        return 0.0
    k = math.cos(math.radians(lat))
    pts = [((v[0] - lon) * k, v[1] - lat) for v in ring]
    best = None
    for i in range(len(pts)):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % len(pts)]
        d = _seg_dist(0.0, 0.0, ax, ay, bx, by)
        if best is None or d < best:
            best = d
    return best * math.pi / 180.0 * _R


def centroid(ring):
    verts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    return (
        sum(v[1] for v in verts) / len(verts),
        sum(v[0] for v in verts) / len(verts),
    )


def format_distance(m):
    if m < 100:
        return f"{round(m / 5) * 5} m"
    if m < 1000:
        return f"{round(m / 10) * 10} m"
    return f"{m / 1000:.1f}".replace(".", ",") + " km"


def describe_offset(from_lat, from_lon, to_lat, to_lon):
    d = haversine_m(from_lat, from_lon, to_lat, to_lon)
    if d < 5:
        return "aquí mismo"
    return f"{format_distance(d)} al {cardinal_es(bearing_deg(from_lat, from_lon, to_lat, to_lon))}"
