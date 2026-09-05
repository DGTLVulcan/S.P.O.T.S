"""Exterior ballistics: where the bullet goes, and what to dial.

A point-mass trajectory integrated against the standard G1/G7 drag
functions, with real air density and a wind vector. Nothing here is
guessed at: the drag tables are the published BRL data, the atmosphere is
the ideal gas law with a humidity term, and the integrator is checked
against the closed-form vacuum parabola in the tests.

What it cannot do is tell you your muzzle velocity or your bullet's BC.
Those come off the box or a chronograph, and a solution is only ever as
good as they are -- which is what true_muzzle_velocity() is for: it bends
the solution to fit impacts you have actually recorded, rather than
asking you to trust the inputs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------------
# Units. Distance is metric because that is how ranges are marked here;
# velocity and bullet weight stay imperial because that is how boxes and
# chronographs are labelled.
# ---------------------------------------------------------------------

FPS_TO_MS = 0.3048
GRAINS_TO_KG = 6.479891e-5
INCH_TO_M = 0.0254
# A ballistic coefficient is published in lb/in2; the solver works in SI.
BC_LB_IN2_TO_KG_M2 = 0.45359237 / (INCH_TO_M ** 2)

GRAVITY = 9.80665
MOA_PER_RAD = 180.0 * 60.0 / math.pi          # 3437.75
MRAD_PER_RAD = 1000.0

# Conditions the drag tables are referenced to (ICAO standard).
STANDARD_DENSITY = 1.225
STANDARD_TEMP_C = 15.0
STANDARD_PRESSURE_HPA = 1013.25

# Below this Mach the bullet is transonic and the solution stops being
# trustworthy -- the drag functions are least reliable through the
# transition, and real bullets can go unstable there.
TRANSONIC_MACH = 1.2

# ---------------------------------------------------------------------
# Drag functions: Mach number -> drag coefficient of the standard
# projectile. G1 is the old flat-base reference, G7 the boat-tail one that
# suits modern match bullets; a BC quoted for one is not valid for the
# other, which is why the model travels with the number.
# ---------------------------------------------------------------------

G1_TABLE = (
    (0.00, 0.2629), (0.05, 0.2558), (0.10, 0.2487), (0.15, 0.2413),
    (0.20, 0.2344), (0.25, 0.2278), (0.30, 0.2214), (0.35, 0.2155),
    (0.40, 0.2104), (0.45, 0.2061), (0.50, 0.2032), (0.55, 0.2020),
    (0.60, 0.2034), (0.70, 0.2165), (0.725, 0.2230), (0.75, 0.2313),
    (0.775, 0.2417), (0.80, 0.2546), (0.825, 0.2706), (0.85, 0.2901),
    (0.875, 0.3136), (0.90, 0.3415), (0.925, 0.3734), (0.95, 0.4084),
    (0.975, 0.4448), (1.00, 0.4805), (1.025, 0.5136), (1.05, 0.5427),
    (1.075, 0.5677), (1.10, 0.5883), (1.125, 0.6053), (1.15, 0.6191),
    (1.20, 0.6393), (1.25, 0.6518), (1.30, 0.6589), (1.35, 0.6621),
    (1.40, 0.6625), (1.50, 0.6573), (1.55, 0.6528), (1.60, 0.6474),
    (1.65, 0.6413), (1.70, 0.6347), (1.75, 0.6280), (1.80, 0.6210),
    (1.85, 0.6141), (1.90, 0.6072), (1.95, 0.6003), (2.00, 0.5934),
    (2.05, 0.5867), (2.10, 0.5804), (2.15, 0.5743), (2.20, 0.5685),
    (2.25, 0.5630), (2.30, 0.5577), (2.35, 0.5527), (2.40, 0.5481),
    (2.45, 0.5438), (2.50, 0.5397), (2.60, 0.5325), (2.70, 0.5264),
    (2.80, 0.5211), (2.90, 0.5168), (3.00, 0.5133), (3.10, 0.5105),
    (3.20, 0.5084), (3.30, 0.5067), (3.40, 0.5054), (3.50, 0.5040),
    (3.60, 0.5030), (3.70, 0.5022), (3.80, 0.5016), (3.90, 0.5010),
    (4.00, 0.5006), (4.20, 0.4998), (4.40, 0.4995), (4.60, 0.4992),
    (4.80, 0.4990), (5.00, 0.4988),
)

G7_TABLE = (
    (0.00, 0.1198), (0.05, 0.1197), (0.10, 0.1196), (0.15, 0.1194),
    (0.20, 0.1193), (0.25, 0.1194), (0.30, 0.1194), (0.35, 0.1194),
    (0.40, 0.1193), (0.45, 0.1193), (0.50, 0.1194), (0.55, 0.1193),
    (0.60, 0.1194), (0.65, 0.1197), (0.70, 0.1202), (0.725, 0.1207),
    (0.75, 0.1215), (0.775, 0.1226), (0.80, 0.1242), (0.825, 0.1266),
    (0.85, 0.1306), (0.875, 0.1368), (0.90, 0.1464), (0.925, 0.1660),
    (0.95, 0.2054), (0.975, 0.2993), (1.00, 0.3803), (1.025, 0.4015),
    (1.05, 0.4043), (1.075, 0.4034), (1.10, 0.4014), (1.125, 0.3987),
    (1.15, 0.3955), (1.20, 0.3884), (1.25, 0.3810), (1.30, 0.3732),
    (1.35, 0.3657), (1.40, 0.3580), (1.50, 0.3440), (1.55, 0.3376),
    (1.60, 0.3315), (1.65, 0.3260), (1.70, 0.3209), (1.75, 0.3160),
    (1.80, 0.3117), (1.85, 0.3078), (1.90, 0.3042), (1.95, 0.3010),
    (2.00, 0.2980), (2.05, 0.2951), (2.10, 0.2922), (2.15, 0.2892),
    (2.20, 0.2864), (2.25, 0.2835), (2.30, 0.2807), (2.35, 0.2779),
    (2.40, 0.2752), (2.45, 0.2725), (2.50, 0.2697), (2.60, 0.2643),
    (2.70, 0.2592), (2.80, 0.2545), (2.90, 0.2502), (3.00, 0.2463),
    (3.10, 0.2428), (3.20, 0.2393), (3.30, 0.2358), (3.40, 0.2323),
    (3.50, 0.2289), (3.60, 0.2255), (3.70, 0.2220), (3.80, 0.2186),
    (3.90, 0.2152), (4.00, 0.2118), (4.20, 0.2050), (4.40, 0.1984),
    (4.60, 0.1919), (4.80, 0.1855), (5.00, 0.1793),
)

DRAG_MODELS = {"g1": G1_TABLE, "g7": G7_TABLE}


def drag_coefficient(mach: float, model: str = "g7") -> float:
    """Standard-projectile Cd at this Mach, interpolated between table
    points. Outside the table the end values are held, which only happens
    well past anything a rifle produces."""
    table = DRAG_MODELS.get(model, G7_TABLE)
    if mach <= table[0][0]:
        return table[0][1]
    if mach >= table[-1][0]:
        return table[-1][1]
    low = 0
    high = len(table) - 1
    while high - low > 1:
        mid = (low + high) // 2
        if table[mid][0] <= mach:
            low = mid
        else:
            high = mid
    m0, c0 = table[low]
    m1, c1 = table[high]
    return c0 + (c1 - c0) * (mach - m0) / (m1 - m0)


# ---------------------------------------------------------------------
# Atmosphere
# ---------------------------------------------------------------------

@dataclass
class Atmosphere:
    """Air the bullet flies through.

    `pressure_hpa` must be STATION pressure -- what a barometer at the
    firing point reads -- not the sea-level figure a weather forecast
    quotes. Feeding a sea-level number in at altitude makes the air denser
    than it is and over-predicts drop, silently.
    """

    temperature_c: float = STANDARD_TEMP_C
    pressure_hpa: float = STANDARD_PRESSURE_HPA
    humidity_pct: float = 0.0

    @property
    def density(self) -> float:
        """Air density in kg/m3, humidity included -- moist air is lighter
        than dry, which is the opposite of what most people expect."""
        kelvin = self.temperature_c + 273.15
        pressure = self.pressure_hpa * 100.0
        # Buck's equation for saturation vapour pressure.
        saturation = 611.21 * math.exp(
            (18.678 - self.temperature_c / 234.5)
            * (self.temperature_c / (257.14 + self.temperature_c))
        )
        vapour = max(0.0, min(100.0, self.humidity_pct)) / 100.0 * saturation
        dry = pressure - vapour
        return dry / (287.058 * kelvin) + vapour / (461.495 * kelvin)

    @property
    def speed_of_sound(self) -> float:
        """Speed of sound in m/s for this air."""
        return math.sqrt(1.4 * self.pressure_hpa * 100.0 / self.density)

    @property
    def density_ratio(self) -> float:
        return self.density / STANDARD_DENSITY


# ---------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------

@dataclass
class Shot:
    """Everything the solver needs about the rifle, the load and the air."""

    muzzle_velocity_fps: float
    ballistic_coefficient: float
    drag_model: str = "g7"
    bullet_grains: float = 0.0
    bullet_diameter_mm: float = 0.0
    bullet_length_mm: float = 0.0
    sight_height_mm: float = 40.0
    zero_distance_m: float = 100.0
    twist_rate_in: float = 0.0
    # Wind as it is called on a range: speed, and the clock direction it
    # blows FROM. 12 o'clock is a headwind, 3 o'clock comes from the right.
    wind_speed_kph: float = 0.0
    wind_clock: float = 3.0
    look_angle_deg: float = 0.0
    atmosphere: Atmosphere = field(default_factory=Atmosphere)

    def wind_vector(self) -> tuple[float, float]:
        """(downrange, lateral) components of the wind in m/s.

        A 3 o'clock wind blows from the right, so it pushes the bullet
        left: lateral is negative.
        """
        speed = self.wind_speed_kph / 3.6
        angle = math.radians((self.wind_clock % 12.0) * 30.0)
        return -speed * math.cos(angle), -speed * math.sin(angle)


@dataclass
class TrajectoryPoint:
    distance_m: float
    drop_m: float                # relative to the line of sight, up positive
    windage_m: float             # positive is right
    velocity_ms: float
    time_s: float
    energy_j: float
    mach: float

    @property
    def transonic(self) -> bool:
        return self.mach < TRANSONIC_MACH


class BallisticsError(ValueError):
    """Inputs that cannot produce a trajectory at all."""


# ---------------------------------------------------------------------
# The solver
# ---------------------------------------------------------------------

def _validate(shot: Shot) -> None:
    if shot.muzzle_velocity_fps <= 0:
        raise BallisticsError("Muzzle velocity must be greater than zero")
    if shot.ballistic_coefficient <= 0:
        raise BallisticsError("Ballistic coefficient must be greater than zero")
    if shot.drag_model not in DRAG_MODELS:
        raise BallisticsError(f"Drag model must be one of {', '.join(DRAG_MODELS)}")
    if shot.zero_distance_m <= 0:
        raise BallisticsError("Zero distance must be greater than zero")


def _integrate(shot: Shot, launch_angle: float, max_distance_m: float,
               step: float = 0.001) -> list[tuple[float, float, float, float, float]]:
    """March the bullet downrange, returning (x, y, z, speed, t) samples.

    y is measured from the line of sight, so the bullet starts one sight
    height below it. Drag acts along the bullet's path THROUGH THE AIR,
    which is why the wind vector is subtracted before the direction is
    taken -- a headwind slows the bullet as well as a crosswind pushing it.

    Stepped with RK4: plain Euler was out by 1.5 mm at 500 m against the
    closed-form vacuum case, which is nothing ballistically but is pure
    arithmetic error, and there is no reason to carry it.
    """
    air = shot.atmosphere
    density = air.density
    sound = air.speed_of_sound
    bc_si = shot.ballistic_coefficient * BC_LB_IN2_TO_KG_M2
    drag_constant = 0.5 * density * (math.pi / 4.0) / bc_si

    wind_x, wind_z = shot.wind_vector()
    look = math.radians(shot.look_angle_deg)
    # Gravity is vertical; the line of sight is not, when shooting up or
    # down a slope. Rotating it into the sight frame is what makes an
    # inclined shot need less come-up, rather than a fudge factor.
    gx = GRAVITY * math.sin(look)
    gy = -GRAVITY * math.cos(look)

    def accel(vx, vy, vz):
        rel_x = vx - wind_x
        rel_z = vz - wind_z
        rel_speed = math.sqrt(rel_x * rel_x + vy * vy + rel_z * rel_z)
        if rel_speed <= 1e-9:
            return gx, gy, 0.0
        decel = drag_constant * drag_coefficient(rel_speed / sound, shot.drag_model) * rel_speed
        return gx - decel * rel_x, gy - decel * vy, -decel * rel_z

    speed = shot.muzzle_velocity_fps * FPS_TO_MS
    vx = speed * math.cos(launch_angle)
    vy = speed * math.sin(launch_angle)
    vz = 0.0
    x = 0.0
    y = -shot.sight_height_mm / 1000.0
    z = 0.0
    t = 0.0

    samples = [(x, y, z, speed, t)]
    limit = max_distance_m + 1.0
    while x < limit and t < 30.0:
        a1 = accel(vx, vy, vz)
        v1 = (vx, vy, vz)

        v2 = (vx + a1[0] * step / 2, vy + a1[1] * step / 2, vz + a1[2] * step / 2)
        a2 = accel(*v2)

        v3 = (vx + a2[0] * step / 2, vy + a2[1] * step / 2, vz + a2[2] * step / 2)
        a3 = accel(*v3)

        v4 = (vx + a3[0] * step, vy + a3[1] * step, vz + a3[2] * step)
        a4 = accel(*v4)

        x += step / 6.0 * (v1[0] + 2 * v2[0] + 2 * v3[0] + v4[0])
        y += step / 6.0 * (v1[1] + 2 * v2[1] + 2 * v3[1] + v4[1])
        z += step / 6.0 * (v1[2] + 2 * v2[2] + 2 * v3[2] + v4[2])
        vx += step / 6.0 * (a1[0] + 2 * a2[0] + 2 * a3[0] + a4[0])
        vy += step / 6.0 * (a1[1] + 2 * a2[1] + 2 * a3[1] + a4[1])
        vz += step / 6.0 * (a1[2] + 2 * a2[2] + 2 * a3[2] + a4[2])
        t += step

        samples.append((x, y, z, math.sqrt(vx * vx + vy * vy + vz * vz), t))
        if vx <= 0:
            break
    return samples


def _sample_at(samples, distance_m: float):
    """Linear interpolation between the two samples bracketing a range."""
    if distance_m <= 0:
        return samples[0]
    for i in range(1, len(samples)):
        if samples[i][0] >= distance_m:
            x0, y0, z0, s0, t0 = samples[i - 1]
            x1, y1, z1, s1, t1 = samples[i]
            span = x1 - x0
            f = 0.0 if span <= 0 else (distance_m - x0) / span
            return (distance_m,
                    y0 + (y1 - y0) * f,
                    z0 + (z1 - z0) * f,
                    s0 + (s1 - s0) * f,
                    t0 + (t1 - t0) * f)
    return None


def _zero_angle(shot: Shot) -> float:
    """Launch angle that puts the bullet on the line of sight at the zero.

    Solved rather than approximated: the angle is tiny but the drop at
    500 m is not, and a closed-form guess drifts badly once drag is in.
    """
    low, high = math.radians(-0.5), math.radians(3.0)
    for _ in range(40):
        mid = (low + high) / 2.0
        samples = _integrate(shot, mid, shot.zero_distance_m)
        point = _sample_at(samples, shot.zero_distance_m)
        if point is None:
            low = mid            # didn't even reach the zero; aim higher
            continue
        if point[1] < 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def solve(shot: Shot, distances_m) -> list[TrajectoryPoint]:
    """Trajectory at each distance, relative to the line of sight."""
    _validate(shot)
    distances = sorted({float(d) for d in distances_m if float(d) > 0})
    if not distances:
        return []

    angle = _zero_angle(shot)
    samples = _integrate(shot, angle, distances[-1])
    sound = shot.atmosphere.speed_of_sound
    mass = shot.bullet_grains * GRAINS_TO_KG

    points = []
    for distance in distances:
        found = _sample_at(samples, distance)
        if found is None:
            break                # the bullet never got this far
        _, y, z, speed, t = found
        points.append(TrajectoryPoint(
            distance_m=distance,
            drop_m=y,
            windage_m=z + _spin_drift(shot, t),
            velocity_ms=speed,
            time_s=t,
            energy_j=0.5 * mass * speed * speed,
            mach=speed / sound,
        ))
    return points


def _spin_drift(shot: Shot, time_s: float) -> float:
    """Litz's approximation, in metres. Right-hand twist pushes right.

    Needs the bullet's length to work out how stable it is, so it is
    simply left out when that is unknown rather than being guessed -- at
    500 m it is a few centimetres either way.
    """
    stability = gyroscopic_stability(shot)
    if stability is None or time_s <= 0:
        return 0.0
    inches = 1.25 * (stability + 1.2) * (time_s ** 1.83)
    return inches * INCH_TO_M


def gyroscopic_stability(shot: Shot) -> float | None:
    """Miller's stability factor, or None if the bullet isn't described
    well enough to work it out."""
    if not (shot.twist_rate_in > 0 and shot.bullet_length_mm > 0
            and shot.bullet_diameter_mm > 0 and shot.bullet_grains > 0):
        return None
    diameter_in = shot.bullet_diameter_mm / 25.4
    length_cal = (shot.bullet_length_mm / 25.4) / diameter_in
    twist_cal = shot.twist_rate_in / diameter_in
    stability = (30.0 * shot.bullet_grains) / (
        twist_cal ** 2 * diameter_in ** 3 * length_cal * (1.0 + length_cal ** 2)
    )
    # Corrected to the actual muzzle velocity; Miller's constant assumes
    # 2800 fps.
    return stability * (shot.muzzle_velocity_fps / 2800.0) ** (1.0 / 3.0)


# ---------------------------------------------------------------------
# Angles and clicks
# ---------------------------------------------------------------------

def to_angle(offset_m: float, distance_m: float, unit: str) -> float:
    """A miss of `offset_m` at `distance_m`, in MOA or mrad."""
    if distance_m <= 0:
        return 0.0
    radians = math.atan2(offset_m, distance_m)
    return radians * (MRAD_PER_RAD if unit == "mrad" else MOA_PER_RAD)


def to_clicks(angle: float, click_value: float) -> float | None:
    if not click_value or click_value <= 0:
        return None
    return angle / click_value


def dope_row(point: TrajectoryPoint, unit: str, click_value: float = 0.0) -> dict:
    """One line of a come-up card.

    Elevation is what you dial, so it is the opposite sign to the drop:
    a bullet 1 mrad low needs 1 mrad UP.
    """
    elevation = to_angle(-point.drop_m, point.distance_m, unit)
    windage = to_angle(-point.windage_m, point.distance_m, unit)
    return {
        "distance_m": round(point.distance_m, 1),
        "drop_cm": round(point.drop_m * 100.0, 1),
        "windage_cm": round(point.windage_m * 100.0, 1),
        "elevation": round(elevation, 2),
        "windage": round(windage, 2),
        "elevation_clicks": (None if to_clicks(elevation, click_value) is None
                             else round(to_clicks(elevation, click_value))),
        "windage_clicks": (None if to_clicks(windage, click_value) is None
                           else round(to_clicks(windage, click_value))),
        "velocity_fps": round(point.velocity_ms / FPS_TO_MS),
        "velocity_ms": round(point.velocity_ms, 1),
        "energy_j": round(point.energy_j),
        "time_s": round(point.time_s, 3),
        "mach": round(point.mach, 2),
        "transonic": point.transonic,
    }


def card(shot: Shot, distances_m, unit: str = "mrad",
         click_value: float = 0.0) -> dict:
    """A full come-up card, plus what the solution is worth."""
    points = solve(shot, distances_m)
    rows = [dope_row(p, unit, click_value) for p in points]
    stability = gyroscopic_stability(shot)
    transonic_at = next((r["distance_m"] for r in rows if r["transonic"]), None)
    return {
        "unit": unit,
        "click_value": click_value,
        "rows": rows,
        "transonic_from_m": transonic_at,
        "stability": None if stability is None else round(stability, 2),
        "air_density": round(shot.atmosphere.density, 4),
        "density_ratio": round(shot.atmosphere.density_ratio, 4),
        "speed_of_sound_ms": round(shot.atmosphere.speed_of_sound, 1),
        "spin_drift_included": stability is not None,
    }


# ---------------------------------------------------------------------
# Truing
# ---------------------------------------------------------------------

def true_muzzle_velocity(shot: Shot, observations, unit: str = "mrad",
                         low_fps: float = 1200.0, high_fps: float = 4500.0):
    """Muzzle velocity that best fits come-ups you actually measured.

    Every solution starts from a number off a box, and boxes are optimistic.
    Rather than trusting it, this bends the one input that is both most
    uncertain and most influential until the predicted come-ups match what
    the rifle really did.

    `observations` is [(distance_m, elevation_actually_needed)] in `unit`.
    Velocity is corrected before BC because a chronograph-free velocity is
    usually wrong by more than a published BC is, and inside 500 m the two
    are hard to tell apart -- past transonic they separate, and that is
    where a BC correction would belong instead.
    """
    rows = [(float(d), float(e)) for d, e in observations if float(d) > 0]
    if not rows:
        raise BallisticsError("Truing needs at least one measured come-up")

    def error(velocity_fps: float) -> float:
        trial = Shot(**{**shot.__dict__, "muzzle_velocity_fps": velocity_fps})
        points = solve(trial, [d for d, _ in rows])
        if len(points) < len(rows):
            return float("inf")
        total = 0.0
        for point, (_, measured) in zip(points, rows):
            predicted = to_angle(-point.drop_m, point.distance_m, unit)
            total += (predicted - measured) ** 2
        return total

    # A ternary search: the squared error is smooth and single-minimum in
    # velocity, so this converges without needing a derivative.
    low, high = low_fps, high_fps
    for _ in range(80):
        a = low + (high - low) / 3.0
        b = high - (high - low) / 3.0
        if error(a) <= error(b):
            high = b
        else:
            low = a
    best = (low + high) / 2.0
    residual = error(best)
    return {
        "muzzle_velocity_fps": round(best, 1),
        "was_fps": round(shot.muzzle_velocity_fps, 1),
        "change_fps": round(best - shot.muzzle_velocity_fps, 1),
        "rms_error": round(math.sqrt(residual / len(rows)), 3),
        "unit": unit,
        "observations": len(rows),
    }
