// Splines and curve modeling.
//
// Editable spline shapes (SplineShape and the Line object) in world space:
// create them from points or Knots, read knots and segments, edit knots and
// topology, sample, QA, and label or pick knots in the agent viewport.
// node.curve_token guards edits against a shape that changed since it was read.
//
// curve_model constructions compile named curves (a small arithmetic language
// over parameters) to an editable spline or a quad cage. The recipe is saved
// in the AppData slot, schema and fingerprints of the curve_model tool, so
// either side can read and update what the other created.
//
// Spline, knot and segment indices are 0-based, like the lists that hold them
// (MAXScript's are 1-based). Edits change the spline base beneath modifiers.
#include "mx_construction.h"

// What the spline and curve members take and give, named for their signatures.
// Each text is read off the code that parses or builds the value.

// The knots of a new or rewritten spline, a list or tuple with one entry per
// knot (ParsePoints, SetKnots). An entry (KnotFrom) is a Knot, a point, or a
// dict of Knot fields: 'pos', and optionally 'type', 'in_vec', 'out_vec', 'seg'.
MX_ARG_TYPE(KnotsArg, "collections.abc.Sequence[_mx.Knot | _mx.Point3 | dict[str, _mx.Point3 | str | None]]")
// A curve_model definition (BuildModel): 'curves' {name: curve dict} and
// 'output' {'kind': 'curve' | 'sweep' | 'loft', ...}, and optionally
// 'tolerance', a number or an expression over the parameters.
MX_ARG_TYPE(CurveDefinitionArg, "dict[str, dict | float | str]")
// What an entry of Spline.pick holds (Pick): 'knot' its index, 'component'
// and 'type' text, 'point' the world position, 'distance' in pixels and
// 'image' the (x, y) fractions of the capture.
MX_RESULT_TYPE(SplinePickField, "int | str | float | _mx.Point3 | tuple[float, float]")

#include <iInstanceMgr.h>
#include <iepoly.h>
#include <mnmesh.h>
#include <splshape.h>

#include <array>
#include <functional>
#include <limits>
#include <map>

#pragma comment(lib, "mnmath.lib")

namespace mx {
inline namespace MX_ABI_TAG {
namespace {

using json = nlohmann::json;

constexpr double kEps = 1e-8;
constexpr double kPi = 3.141592653589793;

// ── Vectors ──────────────────────────────────────────────────────
// Construction and analysis run in double precision.

struct V3 {
    double x = 0, y = 0, z = 0;
    double operator[](int axis) const { return axis == 0 ? x : axis == 1 ? y : z; }
};

V3 operator+(const V3& a, const V3& b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
V3 operator-(const V3& a, const V3& b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
V3 operator*(const V3& a, double k) { return {a.x * k, a.y * k, a.z * k}; }
bool operator==(const V3& a, const V3& b) { return a.x == b.x && a.y == b.y && a.z == b.z; }
bool operator!=(const V3& a, const V3& b) { return !(a == b); }
double Dot(const V3& a, const V3& b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
V3 Cross(const V3& a, const V3& b) { return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
double Len(const V3& a) { return std::sqrt(Dot(a, a)); }
V3 Lerp(const V3& a, const V3& b, double t) { return a * (1 - t) + b * t; }

V3 Unit(const V3& a) {
    const double n = Len(a);
    if (n <= kEps) throw BadParam("A direction or segment has zero length");
    return a * (1 / n);
}

V3 FromP3(const Point3& p) { return {p.x, p.y, p.z}; }
Point3 ToP3(const V3& v) { return Point3(float(v.x), float(v.y), float(v.z)); }
nb::object Tuple(const V3& v) { return MakePoint3(v.x, v.y, v.z); }
double Radians(double degrees) { return degrees * (kPi / 180); }
double Degrees(double radians) { return radians * (180 / kPi); }

V3 Center(const std::vector<V3>& points) {
    V3 lo = points.front(), hi = points.front();
    for (const V3& p : points) {
        lo = {std::min(lo.x, p.x), std::min(lo.y, p.y), std::min(lo.z, p.z)};
        hi = {std::max(hi.x, p.x), std::max(hi.y, p.y), std::max(hi.z, p.z)};
    }
    return {(lo.x + hi.x) / 2, (lo.y + hi.y) / 2, (lo.z + hi.z) / 2};
}

// ── Cubic curves ─────────────────────────────────────────────────

struct Cubic {
    V3 start, out, in, end;
    std::string label;
    bool straight = false;
};

Cubic Chord(const V3& a, const V3& b, const std::string& label = "") {
    return {a, Lerp(a, b, 1.0 / 3), Lerp(a, b, 2.0 / 3), b, label, true};
}

Cubic StraightLine(const V3& a, const V3& b, const std::string& label) {
    if (Len(a - b) <= kEps) throw BadParam("Zero-length line; remove coincident points");
    return Chord(a, b, label);
}

struct Curve {
    std::vector<Cubic> segments;
    bool closed = false;
};

struct CurveKnot {
    V3 pos, in, out;
    bool line = false;  // the segment leaving the knot is straight
};

// Bezier-corner knots that reproduce the cubics exactly.
std::vector<CurveKnot> KnotsOf(const Curve& curve) {
    const auto& s = curve.segments;
    std::vector<CurveKnot> knots;
    for (size_t i = 0; i < s.size(); ++i) {
        const Cubic* previous = i ? &s[i - 1] : curve.closed ? &s.back() : nullptr;
        knots.push_back({s[i].start, previous ? previous->in : s[i].start, s[i].out, s[i].straight});
    }
    if (!curve.closed) knots.push_back({s.back().end, s.back().in, s.back().end, true});
    return knots;
}

constexpr size_t kSampleBudget = 4096;
constexpr int kMaxSamples = 1000;

// Subdivides until the control polygon is within tolerance of the chord; the
// polygon's excess length also catches collinear backtracking.
void Flatten(const V3& a, const V3& b, const V3& c, const V3& d, int depth, double tolerance, size_t budget,
             std::vector<V3>& points) {
    const V3 chord = d - a;
    const double norm = Len(chord);
    double error = Len(a - b) + Len(b - c) + Len(c - d) - norm;
    if (norm > kEps) error = std::max({error, Len(Cross(b - a, chord)) / norm, Len(Cross(c - a, chord)) / norm});
    if (error <= tolerance) {
        points.push_back(d);
        return;
    }
    if (depth >= 16 || points.size() > budget)
        throw BadParam("Curve sampling budget exceeded; increase tolerance or simplify the curve");
    const V3 ab = Lerp(a, b, .5), bc = Lerp(b, c, .5), cd = Lerp(c, d, .5);
    const V3 abc = Lerp(ab, bc, .5), bcd = Lerp(bc, cd, .5), middle = Lerp(abc, bcd, .5);
    Flatten(a, ab, abc, middle, depth + 1, tolerance, budget, points);
    Flatten(middle, bcd, cd, d, depth + 1, tolerance, budget, points);
}

std::vector<V3> Flattened(const Curve& curve, double tolerance, size_t budget = kSampleBudget) {
    std::vector<V3> points{curve.segments.front().start};
    for (const Cubic& s : curve.segments) Flatten(s.start, s.out, s.in, s.end, 0, tolerance, budget, points);
    if (curve.closed) points.back() = points.front();
    return points;
}

int InRange(long long value, const std::string& label, int low, int high) {
    if (value < low || value > high)
        throw BadParam(label + " must be an integer from " + std::to_string(low) + " to " + std::to_string(high));
    return int(value);
}

// Points spaced evenly by length along the curve.
std::vector<V3> Samples(const Curve& curve, int count, double tolerance, const std::string& label = "samples",
                        size_t budget = kSampleBudget) {
    InRange(count, label, curve.closed ? 3 : 2, kMaxSamples);
    const std::vector<V3> points = Flattened(curve, tolerance, budget);
    std::vector<double> distances{0.0};
    for (size_t i = 1; i < points.size(); ++i) distances.push_back(distances.back() + Len(points[i] - points[i - 1]));
    if (distances.back() <= kEps) throw BadParam("Curve has zero length");
    std::vector<V3> out;
    for (int i = 0; i < count; ++i) {
        const double d = distances.back() * i / (curve.closed ? count : count - 1);
        size_t j = size_t(std::lower_bound(distances.begin(), distances.end(), d) - distances.begin());
        j = std::max<size_t>(1, std::min(points.size() - 1, j));
        const double span = distances[j] - distances[j - 1];
        out.push_back(Lerp(points[j - 1], points[j], span > kEps ? (d - distances[j - 1]) / span : 0));
    }
    return out;
}

// Arc length to a ten-thousandth of the segment's control-polygon size.
double CubicLength(const Cubic& c) {
    if (c.straight) return Len(c.end - c.start);
    const double size = std::max({Len(c.out - c.start), Len(c.in - c.out), Len(c.end - c.in)});
    if (size <= 0) return 0;
    std::vector<V3> points{c.start};
    Flatten(c.start, c.out, c.in, c.end, 0, size * 1e-4, std::numeric_limits<size_t>::max(), points);
    double total = 0;
    for (size_t i = 1; i < points.size(); ++i) total += Len(points[i] - points[i - 1]);
    return total;
}

// ── Curve QA ─────────────────────────────────────────────────────

struct Backtrack {
    int segment = 0;
    double t = 0, speed = 0;
};

// Backwards travel along each cubic's endpoint chord, found analytically. It
// can be intended (a looping Bezier); chords of zero length are skipped.
std::vector<Backtrack> Backtracking(const Curve& curve) {
    std::vector<Backtrack> out;
    for (size_t i = 0; i < curve.segments.size(); ++i) {
        const Cubic& s = curve.segments[i];
        const V3 chord = s.end - s.start;
        const double norm2 = Dot(chord, chord);
        if (norm2 <= kEps * kEps) continue;
        const double d0 = Dot(s.out - s.start, chord) / norm2, d1 = Dot(s.in - s.out, chord) / norm2,
                     d2 = Dot(s.end - s.in, chord) / norm2;
        const double a = d0 - 2 * d1 + d2, b = 2 * (d1 - d0), c = d0;
        std::vector<double> candidates{0.0, 1.0};
        if (a > 0 && -b / (2 * a) > 0 && -b / (2 * a) < 1) candidates.push_back(-b / (2 * a));
        double t = candidates[0];
        for (double u : candidates)
            if ((a * u + b) * u + c < (a * t + b) * t + c) t = u;
        const double speed = 3 * ((a * t + b) * t + c);
        if (speed < -1e-9) out.push_back({int(i), t, speed});
    }
    return out;
}

struct TangentBreak {
    int after = 0;
    std::optional<double> angle;  // none where a handle has zero length
};

struct Qa {
    bool closed = false, planar = false, checked = false;
    double length = 0, gap = 0, deviation = 0, tolerance = 0;
    int crossings = 0;
    std::vector<std::pair<int, int>> samples;  // the first crossing polyline spans
    std::vector<TangentBreak> breaks;
    std::vector<Backtrack> backtracking;
    int spans = 0;
    V3 lo, hi;
};

void FindCrossings(const std::vector<V3>& points, const V3& normal, bool closed, Qa& q) {
    int drop = 0;
    for (int i = 1; i < 3; ++i)
        if (std::abs(normal[i]) > std::abs(normal[drop])) drop = i;
    const int u = drop == 0 ? 1 : 0, v = drop == 2 ? 1 : 2;
    auto orient = [&](const V3& a, const V3& b, const V3& c) {
        return (b[u] - a[u]) * (c[v] - a[v]) - (b[v] - a[v]) * (c[u] - a[u]);
    };
    auto within = [&](const V3& a, const V3& b, const V3& c, int k) {
        return std::min(a[k], b[k]) - kEps <= c[k] && c[k] <= std::max(a[k], b[k]) + kEps;
    };
    auto touches = [&](const V3& a, const V3& b, const V3& c) {
        return std::abs(orient(a, b, c)) <= kEps && within(a, b, c, u) && within(a, b, c, v);
    };
    const int n = int(points.size());
    for (int i = 0; i + 1 < n; ++i) {
        const V3 &a = points[i], &b = points[i + 1];
        for (int j = i + 2; j < n - 1; ++j) {
            if (closed && i == 0 && j == n - 2) continue;
            const V3 &c = points[j], &d = points[j + 1];
            const bool proper = orient(a, b, c) * orient(a, b, d) < 0 && orient(c, d, a) * orient(c, d, b) < 0;
            if (proper || touches(a, b, c) || touches(a, b, d) || touches(c, d, a) || touches(c, d, b)) {
                ++q.crossings;
                if (q.samples.size() < 12) q.samples.push_back({i, j});
            }
        }
    }
}

// Sampled QA: planarity, polyline self-crossings, tangent breaks between
// segments and analytic backtracking. Not an exact intersection test.
Qa CurveQa(const Curve& curve, double tolerance) {
    const std::vector<V3> points = Flattened(curve, tolerance);
    if (points.size() > 1025)
        throw BadParam("Curve QA exceeds 1024 spans; increase tolerance or split the construction");
    Qa q;
    q.closed = curve.closed;
    q.tolerance = tolerance;
    const V3 base = points[0];
    V3 longest;
    for (const V3& p : points)
        if (Len(p - base) > Len(longest)) longest = p - base;
    V3 normal;
    for (const V3& p : points)
        if (Len(Cross(longest, p - base)) > Len(normal)) normal = Cross(longest, p - base);
    q.planar = Len(normal) <= kEps;
    if (!q.planar) {
        normal = Unit(normal);
        for (const V3& p : points) q.deviation = std::max(q.deviation, std::abs(Dot(p - base, normal)));
        q.planar = q.deviation <= tolerance;
    }
    q.checked = q.planar && Len(normal) > kEps;
    if (q.checked) FindCrossings(points, normal, curve.closed, q);
    const auto& segs = curve.segments;
    for (size_t i = 0; i < (curve.closed ? segs.size() : segs.size() - 1); ++i) {
        const Cubic &a = segs[i], &b = segs[(i + 1) % segs.size()];
        const V3 incoming = a.end - a.in, outgoing = b.out - b.start;
        if (std::min(Len(incoming), Len(outgoing)) <= kEps) {
            q.breaks.push_back({int(i), std::nullopt});
            continue;
        }
        const double angle = Degrees(std::acos(std::clamp(Dot(Unit(incoming), Unit(outgoing)), -1.0, 1.0)));
        if (angle > .1) q.breaks.push_back({int(i), angle});
    }
    for (size_t i = 1; i < points.size(); ++i) q.length += Len(points[i] - points[i - 1]);
    q.gap = Len(points.front() - points.back());
    q.spans = int(points.size()) - 1;
    q.lo = q.hi = points[0];
    for (const V3& p : points) {
        q.lo = {std::min(q.lo.x, p.x), std::min(q.lo.y, p.y), std::min(q.lo.z, p.z)};
        q.hi = {std::max(q.hi.x, p.x), std::max(q.hi.y, p.y), std::max(q.hi.z, p.z)};
    }
    q.backtracking = Backtracking(curve);
    return q;
}

nb::list BacktrackList(const std::vector<Backtrack>& rows) {
    nb::list out;
    for (const Backtrack& b : rows) {
        nb::dict row;
        row["segment"] = b.segment;
        row["t"] = b.t;
        row["min_forward_derivative"] = b.speed;
        out.append(row);
    }
    return out;
}

void PutQa(nb::dict& d, const Qa& q) {
    d["complete"] = true;
    d["closed"] = q.closed;
    d["length"] = q.length;
    d["endpoint_gap"] = q.gap;
    d["planar"] = q.planar;
    d["planarity_deviation"] = q.deviation;
    d["sampled_intersections"] = q.crossings;
    nb::list samples;
    for (const auto& [i, j] : q.samples) samples.append(nb::make_tuple(i, j));
    d["intersection_samples"] = samples;
    d["intersection_check"] = q.checked ? "planar polyline approximation" : "not checked: nonplanar or collinear";
    nb::list breaks;
    for (const TangentBreak& b : q.breaks) {
        nb::dict row;
        row["after_segment"] = b.after;
        row["angle"] = b.angle ? nb::object(nb::float_(*b.angle)) : nb::none();
        breaks.append(row);
    }
    d["tangent_breaks"] = breaks;
    d["segment_backtracking"] = BacktrackList(q.backtracking);
    d["backtracking_check"] = "analytic chord-projected derivative; zero-length chords skipped; may be intentional";
    d["tolerance"] = q.tolerance;
    d["sampled_spans"] = q.spans;
    d["bounds"] = nb::make_tuple(Tuple(q.lo), Tuple(q.hi));
}

// ── Definition values ────────────────────────────────────────────

bool IsNumber(const json& j) {
    const auto t = j.type();
    return t == json::value_t::number_integer || t == json::value_t::number_unsigned || t == json::value_t::number_float;
}

double Num(const json& value, const std::string& label) {
    if (!IsNumber(value)) throw BadParam(label + " must be a finite number with magnitude at most 1e12");
    return Finite(value.get<double>(), label);
}

json Field(const json& object, const char* key, const json& fallback = json()) {
    const auto it = object.find(key);
    return it == object.end() ? fallback : *it;
}

std::vector<std::string> Words(const std::string& text) {
    std::vector<std::string> words;
    size_t at = 0;
    while (at < text.size()) {
        const size_t end = std::min(text.find(' ', at), text.size());
        if (end > at) words.push_back(text.substr(at, end - at));
        at = end + 1;
    }
    return words;
}

void Keys(const json& value, const std::string& allowed, const std::string& label) {
    if (value.type() != json::value_t::object) throw BadParam(label + " must be a dict");
    const std::vector<std::string> names = Words(allowed);
    std::vector<std::string> extra;
    for (auto it = value.begin(); it != value.end(); ++it)
        if (std::find(names.begin(), names.end(), it.key()) == names.end()) extra.push_back(it.key());
    if (!extra.empty())
        throw BadParam("Unknown " + label + " fields: " + Join(extra) + "." + Suggest(extra.front(), names) +
                       " Fields: " + Join(names));
}

bool Flag(const json& value, const std::string& label) {
    if (value.type() != json::value_t::boolean) throw BadParam(label + " must be boolean");
    return value.get<bool>();
}

int JsonInt(const json& value, const std::string& label, int low, int high) {
    const auto t = value.type();
    if (t != json::value_t::number_integer && t != json::value_t::number_unsigned)
        throw BadParam(label + " must be an integer from " + std::to_string(low) + " to " + std::to_string(high));
    return InRange(value.get<long long>(), label, low, high);
}

Values ParseValues(const json& parameters) {
    Values out;
    if (parameters.type() == json::value_t::null) return out;
    if (parameters.type() != json::value_t::object || parameters.size() > 64)
        throw BadParam("parameters must be a dict of at most 64 numbers: {'width': 4}");
    for (auto it = parameters.begin(); it != parameters.end(); ++it) {
        const std::string& key = it.key();
        if (!IsParameterName(key)) throw BadParam(kParameterNameRule);
        out[key] = Num(it.value(), "parameters." + key);
    }
    return out;
}

// A number, or an arithmetic expression over the parameters: "width/2".
double Coordinate(const json& value, const Values& values) {
    if (value.type() != json::value_t::string) return Num(value, "section coordinate");
    return Expression(value.get_ref<const std::string&>(), values);
}

V3 Vec(const json& value, const Values& values) {
    if (value.type() != json::value_t::array || (value.size() != 2 && value.size() != 3))
        throw BadParam("Curve points/directions need two or three coordinates");
    return {Coordinate(value[0], values), Coordinate(value[1], values),
            value.size() == 3 ? Coordinate(value[2], values) : 0.0};
}

// ── Curve kinds ──────────────────────────────────────────────────

struct Frame {
    V3 origin, x, y, z;
    V3 World(const V3& p) const { return origin + (x * p.x + (y * p.y + z * p.z)); }
};

Frame PlaneFrame(const json& spec, const Values& values) {
    if (spec.type() == json::value_t::null || spec.type() == json::value_t::string) {
        const std::string name = spec.type() == json::value_t::null ? "xy" : Lower(spec.get<std::string>());
        V3 x{1, 0, 0}, z{0, 0, 1};
        if (name == "xz") z = {0, -1, 0};
        else if (name == "yz") { x = {0, 1, 0}; z = {1, 0, 0}; }
        else if (name != "xy") throw BadParam("plane must be xy, xz, yz, or {origin,x_axis,normal}");
        return {{}, x, Cross(z, x), z};
    }
    Keys(spec, "origin x_axis normal", "plane");
    const V3 origin = Vec(Field(spec, "origin", json::array({0, 0, 0})), values);
    const V3 x = Unit(Vec(Field(spec, "x_axis", json::array({1, 0, 0})), values));
    const V3 z = Unit(Vec(Field(spec, "normal", json::array({0, 0, 1})), values));
    if (std::abs(Dot(x, z)) > 1e-6) throw BadParam("plane.x_axis must be perpendicular to plane.normal");
    return {origin, x, Unit(Cross(z, x)), z};
}

// Cubic approximations of a circular arc, one per 90 degrees or less.
std::vector<Cubic> ArcPieces(const V3& center, double radius, double start, double sweep, const std::string& label) {
    if (radius <= kEps || !(kEps < std::abs(sweep) && std::abs(sweep) <= 360))
        throw BadParam("Arc requires positive radius and nonzero sweep within +/-360 degrees");
    const int count = int(std::ceil(std::abs(sweep) / 90));
    std::vector<Cubic> pieces;
    for (int i = 0; i < count; ++i) {
        const double a = Radians(start + sweep * i / count), b = Radians(start + sweep * (i + 1) / count);
        const double h = 4.0 / 3 * std::tan((b - a) / 4) * radius;
        const V3 p = center + V3{radius * std::cos(a), radius * std::sin(a), 0};
        const V3 q = center + V3{radius * std::cos(b), radius * std::sin(b), 0};
        pieces.push_back({p, p + V3{-h * std::sin(a), h * std::cos(a), 0}, q - V3{-h * std::sin(b), h * std::cos(b), 0},
                          q, label});
    }
    return pieces;
}

std::vector<Cubic> ArcBetween(const V3& a, const V3& b, double radius, bool clockwise, const std::string& label) {
    if (std::abs(a.z - b.z) > kEps) throw BadParam("Radius arcs must lie in the sketch XY plane");
    const V3 delta = b - a;
    const double chord = Len(delta);
    if (radius <= kEps || chord <= kEps || chord > 2 * radius + kEps)
        throw BadParam("Arc radius is too small for its chord");
    const V3 normal{-delta.y / chord, delta.x / chord, 0};
    const double sign = clockwise ? -1 : 1;
    const V3 center = Lerp(a, b, .5) + normal * (sign * std::sqrt(std::max(0.0, radius * radius - chord * chord / 4)));
    const double start = Degrees(std::atan2(a.y - center.y, a.x - center.x));
    const double sweep = sign * Degrees(2 * std::asin(std::min(1.0, chord / (2 * radius))));
    return ArcPieces(center, radius, start, sweep, label);
}

std::vector<Cubic> TangentArc(const V3& a, const V3& b, const V3& tangent, const std::string& label) {
    const V3 t = Unit(tangent);
    if (std::abs(t.z) > kEps || std::abs(a.z - b.z) > kEps)
        throw BadParam("Tangent arcs must lie in the sketch XY plane");
    const V3 d = b - a, n{-t.y, t.x, 0};
    const double denominator = 2 * Dot(d, n);
    if (std::abs(denominator) <= kEps) throw BadParam("Tangent arc is straight/undefined; use a line");
    const double signedRadius = Dot(d, d) / denominator;
    const V3 c = a + n * signedRadius;
    const double start = Degrees(std::atan2(a.y - c.y, a.x - c.x));
    const double end = Degrees(std::atan2(b.y - c.y, b.x - c.x));
    const double sweep = signedRadius > 0 ? PyMod(end - start, 360) : -PyMod(start - end, 360);
    return ArcPieces(c, std::abs(signedRadius), start, sweep, label);
}

std::vector<Cubic> Fillet(const std::vector<V3>& points, double radius, bool closed) {
    if (radius <= kEps) throw BadParam("fillet must be positive; omit it for sharp corners");
    const size_t n = points.size();
    struct Corner { V3 in, out; std::vector<Cubic> arc; };
    std::vector<Corner> corners;
    for (size_t i = 0; i < n; ++i) {
        const V3& p = points[i];
        if (!closed && (i == 0 || i == n - 1)) {
            corners.push_back({p, p, {}});
            continue;
        }
        const V3 u = Unit(points[(i + n - 1) % n] - p), v = Unit(points[(i + 1) % n] - p);
        const double theta = std::acos(std::clamp(Dot(u, v), -1.0, 1.0));
        if (theta <= kEps || kPi - theta <= kEps) throw BadParam("Fillet needs a noncollinear corner");
        const double distance = radius / std::tan(theta / 2);
        const V3 a = p + u * distance, b = p + v * distance;
        const V3 c = p + Unit(u + v) * (radius / std::sin(theta / 2));
        const double start = Degrees(std::atan2(a.y - c.y, a.x - c.x));
        const double sign = Cross(u * -1, v).z > 0 ? 1 : -1;
        corners.push_back({a, b, ArcPieces(c, radius, start, sign * (180 - Degrees(theta)), "corner_" + std::to_string(i + 1))});
    }
    std::vector<Cubic> result;
    for (size_t i = 0; i < (closed ? n : n - 1); ++i) {
        const size_t j = (i + 1) % n;
        if (Dot(corners[j].in - corners[i].out, points[j] - points[i]) <= kEps)
            throw BadParam("Fillets overlap; reduce radius or lengthen the adjacent edges");
        result.push_back(StraightLine(corners[i].out, corners[j].in, "edge_" + std::to_string(i + 1)));
        result.insert(result.end(), corners[j].arc.begin(), corners[j].arc.end());
    }
    return result;
}

// Outward miter offset of a closed polygon; refuses edges that collapse or invert.
std::vector<V3> OffsetPolygon(const std::vector<V3>& points, double amount) {
    const size_t n = points.size();
    double area = 0;
    for (size_t i = 0; i < n; ++i) area += Cross(points[i], points[(i + 1) % n]).z;
    area /= 2;
    if (std::abs(area) <= kEps) throw BadParam("Offset needs a simple closed planar polygon");
    const double sign = area > 0 ? 1 : -1;
    std::vector<V3> out;
    for (size_t i = 0; i < n; ++i) {
        const V3 u = Unit(points[i] - points[(i + n - 1) % n]), v = Unit(points[(i + 1) % n] - points[i]);
        const V3 n1{sign * u.y, -sign * u.x, 0}, n2{sign * v.y, -sign * v.x, 0};
        const double denominator = 1 + Dot(n1, n2);
        if (denominator < 1e-6) throw BadParam("Offset has an unbounded miter at a reversing edge");
        out.push_back(points[i] + (n1 + n2) * (amount / denominator));
    }
    for (size_t i = 0; i < n; ++i)
        if (Dot(out[(i + 1) % n] - out[i], points[(i + 1) % n] - points[i]) <= kEps)
            throw BadParam("Offset collapsed or inverted an edge");
    return out;
}

// Cubic Hermite spans through the points; tension 0 is Catmull-Rom.
std::vector<Cubic> Interpolate(const std::vector<V3>& points, double tension, bool closed) {
    const size_t n = points.size();
    std::vector<V3> tangents;
    for (size_t i = 0; i < n; ++i) {
        const V3& p = points[i];
        const V3& before = i || closed ? points[(i + n - 1) % n] : p;
        const V3& after = closed || i < n - 1 ? points[(i + 1) % n] : p;
        tangents.push_back((after - before) * ((1 - tension) * (before != p && after != p ? .5 : 1)));
    }
    std::vector<Cubic> pieces;
    for (size_t i = 0; i < (closed ? n : n - 1); ++i) {
        const size_t j = (i + 1) % n;
        pieces.push_back({points[i], points[i] + tangents[i] * (1.0 / 3), points[j] - tangents[j] * (1.0 / 3), points[j],
                          "span_" + std::to_string(i + 1)});
    }
    return pieces;
}

std::vector<Cubic> PointCurve(const std::string& kind, const json& spec, const Values& values, bool closed) {
    const json rows = Field(spec, "points");
    if (rows.type() != json::value_t::array || rows.size() < 2 || rows.size() > 256)
        throw BadParam("points needs 2-256 points");
    std::vector<V3> points;
    for (const json& row : rows) points.push_back(Vec(row, values));
    if (closed && points.size() < 3) throw BadParam("Closed curves need at least three points");
    for (size_t i = 1; i < points.size(); ++i)
        if (Len(points[i] - points[i - 1]) <= kEps) throw BadParam("Consecutive curve points coincide");
    if (closed && Len(points.front() - points.back()) <= kEps)
        throw BadParam("Closed curves wrap automatically; omit the duplicated endpoint");
    auto planar = [&] { return std::all_of(points.begin(), points.end(), [](const V3& p) { return std::abs(p.z) <= kEps; }); };
    if (kind == "bezier") {
        if (points.size() != 4 || closed) throw BadParam("bezier needs four control points and closed=false");
        return {{points[0], points[1], points[2], points[3], "bezier"}};
    }
    if (kind == "spline") {
        const double tension = Coordinate(Field(spec, "tension", 0), values);
        if (!(0 <= tension && tension < 1)) throw BadParam("spline tension must be in [0,1)");
        return Interpolate(points, tension, closed);
    }
    if (spec.contains("offset")) {
        if (!closed || !planar()) throw BadParam("offset requires a closed polygon on its local XY plane");
        points = OffsetPolygon(points, Coordinate(spec["offset"], values));
    }
    if (spec.contains("fillet")) {
        if (!planar()) throw BadParam("fillet requires planar XY points");
        return Fillet(points, Coordinate(spec["fillet"], values), closed);
    }
    std::vector<Cubic> pieces;
    for (size_t i = 0; i < (closed ? points.size() : points.size() - 1); ++i)
        pieces.push_back(StraightLine(points[i], points[(i + 1) % points.size()], "edge_" + std::to_string(i + 1)));
    return pieces;
}

// The previous segment's outgoing direction, for segments that continue it.
std::optional<V3> Continuing(const std::vector<Cubic>& pieces) {
    if (pieces.empty()) return std::nullopt;
    return pieces.back().end - pieces.back().in;
}

std::vector<Cubic> PathCurve(const json& spec, const Values& values, bool closed) {
    V3 current = Vec(Field(spec, "start"), values);
    const json rows = Field(spec, "segments");
    if (rows.type() != json::value_t::array || rows.empty() || rows.size() > 256)
        throw BadParam("path.segments needs 1-256 segments");
    const std::map<std::string, std::string> fields = {
        {"line", ""}, {"arc", "radius clockwise"}, {"tangent_arc", "tangent"},
        {"bezier", "start_tangent end_tangent start_length end_length"}};
    std::vector<Cubic> pieces;
    for (size_t i = 0; i < rows.size(); ++i) {
        const json& row = rows[i];
        Keys(row, "kind to radius clockwise tangent start_tangent end_tangent start_length end_length label", "segment");
        const V3 end = Vec(Field(row, "to"), values);
        const json label = Field(row, "label", "segment_" + std::to_string(i + 1));
        if (label.type() != json::value_t::string || label.get<std::string>().empty() || label.get<std::string>().size() > 64)
            throw BadParam("segment label needs 1-64 characters");
        const std::string name = label.get<std::string>();
        const json kind = Field(row, "kind", "line");
        const auto known = kind.type() == json::value_t::string ? fields.find(kind.get<std::string>()) : fields.end();
        if (known == fields.end()) throw BadParam("Segment kind must be line, arc, tangent_arc or bezier");
        Keys(row, "kind to label " + known->second, known->first + " segment");
        std::vector<Cubic> added;
        if (known->first == "line") {
            added = {StraightLine(current, end, name)};
        } else if (known->first == "arc") {
            added = ArcBetween(current, end, Coordinate(Field(row, "radius"), values),
                               Flag(Field(row, "clockwise", false), "clockwise"), name);
        } else if (known->first == "tangent_arc") {
            const std::optional<V3> t = row.contains("tangent") ? Vec(row["tangent"], values) : Continuing(pieces);
            if (!t) throw BadParam("First tangent_arc needs tangent");
            added = TangentArc(current, end, *t, name);
        } else {
            const std::optional<V3> t = row.contains("start_tangent") ? Vec(row["start_tangent"], values) : Continuing(pieces);
            if (!t) throw BadParam("First bezier segment needs start_tangent");
            const V3 endTangent = Vec(Field(row, "end_tangent"), values);
            const double a = Coordinate(Field(row, "start_length", Len(end - current) / 3), values);
            const double b = Coordinate(Field(row, "end_length", Len(end - current) / 3), values);
            if (std::min(a, b) <= kEps) throw BadParam("Bezier handle lengths must be positive");
            added = {{current, current + Unit(*t) * a, end - Unit(endTangent) * b, end, name}};
        }
        pieces.insert(pieces.end(), added.begin(), added.end());
        current = end;
    }
    if (closed && Len(pieces.front().start - pieces.back().end) > kEps)
        pieces.push_back(StraightLine(pieces.back().end, pieces.front().start, "closing_edge"));
    return pieces;
}

Curve MakeCurve(const json& spec, const Values& values) {
    Keys(spec, "kind plane points closed radius width depth start_angle sweep center start segments tension fillet offset",
         "curve");
    const std::map<std::string, std::string> fields = {
        {"polyline", "points fillet offset"}, {"spline", "points tension"}, {"bezier", "points"},
        {"path", "start segments"}, {"circle", "radius center start_angle"},
        {"arc", "radius center start_angle sweep"}, {"rounded_rectangle", "width depth radius"}};
    const json kindValue = Field(spec, "kind");
    const auto known = kindValue.type() == json::value_t::string ? fields.find(kindValue.get<std::string>()) : fields.end();
    if (known == fields.end())
        throw BadParam("Curve kind must be polyline, spline, bezier, path, arc, circle or rounded_rectangle");
    const std::string& kind = known->first;
    Keys(spec, "kind plane closed " + known->second, kind + " curve");
    const Frame frame = PlaneFrame(Field(spec, "plane"), values);
    const bool closed = Flag(Field(spec, "closed", kind == "circle" || kind == "rounded_rectangle"), "closed");
    std::vector<Cubic> pieces;
    if (kind == "circle" || kind == "arc") {
        if (kind == "circle" && !closed) throw BadParam("circle is closed");
        const double sweep = kind == "circle" ? 360 : Coordinate(Field(spec, "sweep", 90), values);
        pieces = ArcPieces(Vec(Field(spec, "center", json::array({0, 0})), values), Coordinate(Field(spec, "radius"), values),
                           Coordinate(Field(spec, "start_angle", 0), values), sweep, "arc");
        if (kind == "arc" && closed && Len(pieces.front().start - pieces.back().end) > kEps)
            throw BadParam("Use a path with a closing line for a closed partial arc");
    } else if (kind == "rounded_rectangle") {
        if (!closed) throw BadParam("rounded_rectangle is closed");
        const double w = Coordinate(Field(spec, "width"), values), d = Coordinate(Field(spec, "depth"), values),
                     r = Coordinate(Field(spec, "radius"), values);
        if (w <= kEps || d <= kEps || !(kEps < r && r < std::min(w, d) / 2))
            throw BadParam("Rounded rectangle needs 0 < radius < half its smaller dimension");
        pieces = Fillet({{-w / 2, -d / 2, 0}, {w / 2, -d / 2, 0}, {w / 2, d / 2, 0}, {-w / 2, d / 2, 0}}, r, true);
    } else if (kind == "path") {
        pieces = PathCurve(spec, values, closed);
    } else {
        pieces = PointCurve(kind, spec, values, closed);
    }
    if (pieces.empty() || pieces.size() > 256) throw BadParam("Construction exceeds 256 cubic segments");
    Curve curve;
    curve.closed = closed;
    for (const Cubic& s : pieces)
        curve.segments.push_back({frame.World(s.start), frame.World(s.out), frame.World(s.in), frame.World(s.end), s.label,
                                  s.straight});
    return curve;
}

// ── Construction models ──────────────────────────────────────────

struct Model {
    std::string kind;  // curve, sweep or loft
    std::vector<std::pair<std::string, Curve>> curves;
    std::vector<Qa> reports;  // parallel to curves
    Curve curve;              // kind curve
    std::vector<V3> vertices;
    std::vector<std::vector<int>> faces;  // 0-based vertex indices
    json alignment;                        // the loft's locked seam shifts, else null
};

struct Stations {
    std::vector<std::vector<V3>> sections;
    bool closedProfile = false, closedPath = false;
};

struct Build {
    Model& model;
    const json& output;
    const Values& values;
    double tolerance = 0;
    int samples = 0;
    bool caps = true;

    size_t Index(const json& key) const {
        std::vector<std::string> names;
        for (size_t i = 0; i < model.curves.size(); ++i) {
            if (key.type() == json::value_t::string && model.curves[i].first == key.get<std::string>()) return i;
            names.push_back(model.curves[i].first);
        }
        const std::string wanted = key.type() == json::value_t::string ? key.get<std::string>() : key.dump();
        throw BadParam("Unknown curve reference: " + key.dump() + "." + Suggest(wanted, names));
    }
    const Curve& Get(const json& key) const { return model.curves[Index(key)].second; }

    // A section: planar, not self-intersecting, closed when capped, with area when closed.
    const Curve& Profile(const json& key) const {
        const size_t i = Index(key);
        const Curve& c = model.curves[i].second;
        const std::string name = model.curves[i].first;
        if (!model.reports[i].planar || model.reports[i].crossings)
            throw BadParam("Profile " + name + " is nonplanar or self-intersecting");
        if (caps && !c.closed) throw BadParam("Caps require closed profiles");
        if (c.closed) {
            const std::vector<V3> ps = Flattened(c, tolerance);
            V3 area;
            for (size_t k = 0; k + 1 < ps.size(); ++k) area = area + Cross(ps[k] - ps[0], ps[k + 1] - ps[0]);
            if (Len(area) <= kEps) throw BadParam("Profile " + name + " encloses zero area");
        }
        return c;
    }
};

V3 Rotate(const V3& v, const V3& axis, double angle) {
    const double c = std::cos(angle), s = std::sin(angle);
    return v * c + Cross(axis, v) * s + axis * (Dot(axis, v) * (1 - c));
}

// Parallel transport of a frame axis from tangent a to tangent b: no roll.
V3 Transport(const V3& v, const V3& a, const V3& b) {
    const V3 axis = Cross(a, b);
    const double sine = Len(axis), cosine = std::clamp(Dot(a, b), -1.0, 1.0);
    if (cosine < -1 + 1e-7) throw BadParam("Sweep path reverses direction; split it or soften the bend");
    return sine <= kEps ? v : Rotate(v, axis * (1 / sine), std::atan2(sine, cosine));
}

Stations SweepStations(const Build& b) {
    const Curve& path = b.Get(Field(b.output, "path"));
    const Curve& profile = b.Profile(Field(b.output, "profile"));
    const int count = JsonInt(Field(b.output, "path_samples", 48), "path_samples", path.closed ? 3 : 2, 512);
    if (count * b.samples > 50000) throw BadParam("Sweep exceeds 50000 vertices");
    const std::vector<V3> pp = Samples(profile, b.samples, b.tolerance);
    for (const V3& p : pp)
        if (std::abs(p.z) > b.tolerance)
            throw BadParam("Sweep profile must be on world XY at z=0; its x/y are section coordinates");
    if (profile.closed) {
        double area = 0;
        for (size_t i = 0; i < pp.size(); ++i) area += Cross(pp[i], pp[(i + 1) % pp.size()]).z;
        if (area <= kEps) throw BadParam("Sweep profile must wind counterclockwise in XY");
    }
    const std::vector<V3> stations = Samples(path, count, b.tolerance);
    const size_t n = stations.size();
    std::vector<V3> tangents;
    for (size_t i = 0; i < n; ++i) {
        const V3& before = i || path.closed ? stations[(i + n - 1) % n] : stations[i];
        const V3& after = path.closed || i < n - 1 ? stations[(i + 1) % n] : stations[i];
        tangents.push_back(Unit(after - before));
    }
    const V3 up = Unit(Vec(Field(b.output, "up", json::array({0, 0, 1})), b.values));
    const V3 y = up - tangents[0] * Dot(up, tangents[0]);
    if (Len(y) <= kEps) throw BadParam("up is parallel to the initial path tangent; choose another up direction");
    std::vector<V3> frames{Unit(Cross(Unit(y), tangents[0]))};
    for (size_t i = 1; i < n; ++i) frames.push_back(Unit(Transport(frames.back(), tangents[i - 1], tangents[i])));
    double correction = 0;
    if (path.closed) {
        const V3 end = Transport(frames.back(), tangents.back(), tangents[0]);
        correction = std::atan2(Dot(tangents[0], Cross(end, frames[0])), Dot(end, frames[0]));
    }
    const double twist = Coordinate(Field(b.output, "twist", 0), b.values);
    if (std::abs(twist) > 36000) throw BadParam("twist must be within +/-36000 degrees");
    const json scales = Field(b.output, "scale", json::array({1, 1}));
    if (scales.type() != json::value_t::array || scales.size() != 2) throw BadParam("scale needs [start,end]");
    const double first = Coordinate(scales[0], b.values), last = Coordinate(scales[1], b.values);
    if (std::min(first, last) <= kEps) throw BadParam("Sweep scale must stay positive");
    if (path.closed && (std::abs(first - last) > kEps || std::abs(twist / 360 - std::round(twist / 360)) > 1e-8))
        throw BadParam("Closed sweep needs equal end scales and whole-turn twist");
    if (path.closed && b.caps) throw BadParam("Closed sweep needs caps=false");
    Stations s;
    for (size_t i = 0; i < n; ++i) {
        const double f = double(i) / (path.closed ? count : count - 1);
        const V3 x = Rotate(frames[i], tangents[i], (Radians(twist) + correction) * f);
        const V3 yAxis = Cross(tangents[i], x);
        const double scale = first + (last - first) * f;
        std::vector<V3> section;
        for (const V3& q : pp) section.push_back(stations[i] + (x * q.x + yAxis * q.y) * scale);
        s.sections.push_back(section);
    }
    s.closedProfile = profile.closed;
    s.closedPath = path.closed;
    return s;
}

V3 Mean(const std::vector<V3>& points) {
    V3 sum;
    for (const V3& p : points) sum = sum + p;
    return sum * (1.0 / points.size());
}

// Sections sampled to matching counts. Seams are cyclic shifts: the stored
// ones on update, else chosen at creation (align auto) and then locked.
Stations LoftStations(const Build& b, const json& stored) {
    const json names = Field(b.output, "sections");
    if (names.type() != json::value_t::array || names.size() < 2 || names.size() > 128)
        throw BadParam("Loft sections needs 2-128 curve names");
    std::vector<const Curve*> curves;
    for (const json& name : names) curves.push_back(&b.Profile(name));
    for (const Curve* c : curves)
        if (c->closed != curves[0]->closed) throw BadParam("Loft sections must share open/closed state");
    Stations s;
    for (const Curve* c : curves) s.sections.push_back(Samples(*c, b.samples, b.tolerance));
    if (curves[0]->closed) {
        std::vector<V3> normals;
        for (const auto& pts : s.sections) {
            V3 n;
            for (size_t k = 0; k < pts.size(); ++k) n = n + Cross(pts[k] - pts[0], pts[(k + 1) % pts.size()] - pts[0]);
            normals.push_back(Unit(n));
        }
        for (size_t i = 1; i < normals.size(); ++i)
            if (Dot(normals[i - 1], normals[i]) <= 0)
                throw BadParam("Adjacent loft profiles reverse winding or turn at least 90 degrees; fix their planes/order");
    }
    const json align = Field(b.output, "align", "start");
    if (align != json("start") && align != json("auto")) throw BadParam("align must be start or auto");
    if (stored.type() != json::value_t::null && (stored.type() != json::value_t::array || stored.size() != curves.size()))
        throw BadParam("Stored loft alignment is invalid");
    const int n = b.samples;
    json chosen = json::array();
    for (size_t i = 0; i < s.sections.size(); ++i) {
        int shift = 0;
        if (stored.type() != json::value_t::null) {
            shift = JsonInt(stored[i], "alignment", 0, n - 1);
        } else if (align == json("auto") && i && curves[i]->closed) {
            const auto& previous = s.sections[i - 1];
            const V3 a = Mean(previous), c = Mean(s.sections[i]);
            double best = std::numeric_limits<double>::infinity();
            for (int k = 0; k < n; ++k) {
                double cost = 0;
                for (int j = 0; j < n; ++j) {
                    const V3 d = (previous[j] - a) - (s.sections[i][(j + k) % n] - c);
                    cost += Dot(d, d);
                }
                if (cost < best) { best = cost; shift = k; }
            }
        }
        std::rotate(s.sections[i].begin(), s.sections[i].begin() + shift, s.sections[i].end());
        chosen.push_back(shift);
    }
    b.model.alignment = chosen;
    s.closedProfile = curves[0]->closed;
    return s;
}

// Quads between corresponding section points; caps close the ends.
void BuildLoft(const Stations& s, bool caps, bool reverse, Model& m) {
    m.faces = LoftQuads(s.sections, {s.closedProfile, s.closedPath, caps, reverse});
    for (const auto& section : s.sections) m.vertices.insert(m.vertices.end(), section.begin(), section.end());
}

// Compiles named curves to a spline or a quad cage; `stored` locks loft seams.
Model BuildModel(const json& definition, const Values& values, const json& stored) {
    Keys(definition, "curves output tolerance", "definition");
    const json specs = Field(definition, "curves");
    if (specs.type() != json::value_t::object || specs.empty() || specs.size() > 32)
        throw BadParam("curves needs 1-32 named definitions");
    for (auto it = specs.begin(); it != specs.end(); ++it)
        if (it.key().empty() || it.key().size() > 64) throw BadParam("Curve names need 1-64 characters");
    const double tolerance = Coordinate(Field(definition, "tolerance", 0.01), values);
    if (!(1e-6 <= tolerance && tolerance <= 1e6)) throw BadParam("tolerance must be between 1e-6 and 1e6 scene units");
    Model m;
    for (auto it = specs.begin(); it != specs.end(); ++it) m.curves.push_back({it.key(), MakeCurve(it.value(), values)});
    for (const auto& [name, curve] : m.curves) m.reports.push_back(CurveQa(curve, tolerance));
    const json output = Field(definition, "output");
    Keys(output, "kind curve path profile sections path_samples profile_samples up twist scale caps align reverse", "output");
    const std::map<std::string, std::string> fields = {
        {"curve", "curve"},
        {"sweep", "path profile path_samples profile_samples up twist scale caps reverse"},
        {"loft", "sections profile_samples align caps reverse"}};
    const json kind = Field(output, "kind");
    const auto known = kind.type() == json::value_t::string ? fields.find(kind.get<std::string>()) : fields.end();
    if (known == fields.end()) throw BadParam("output.kind must be curve, sweep or loft");
    m.kind = known->first;
    Keys(output, "kind " + known->second, m.kind + " output");
    Build b{m, output, values, tolerance};
    if (m.kind == "curve") {
        m.curve = b.Get(Field(output, "curve"));
        return m;
    }
    b.samples = JsonInt(Field(output, "profile_samples", 32), "profile_samples", 3, 256);
    b.caps = Flag(Field(output, "caps", true), "caps");
    const bool reverse = Flag(Field(output, "reverse", false), "reverse");
    BuildLoft(m.kind == "sweep" ? SweepStations(b) : LoftStations(b, stored), b.caps, reverse, m);
    return m;
}

// Knot segment types for a spline, or vertex count and faces for a cage:
// what parameter updates must leave unchanged.
std::string TopologyText(const Model& m) {
    json value = json::array();
    if (m.kind == "curve") {
        value.push_back(m.curve.closed);
        json segments = json::array();
        for (const CurveKnot& k : KnotsOf(m.curve)) segments.push_back(k.line ? "line" : "curve");
        value.push_back(segments);
    } else {
        value.push_back(m.vertices.size());
        json faces = json::array();
        for (const auto& face : m.faces) {
            json ids = json::array();
            for (int v : face) ids.push_back(v + 1);
            faces.push_back(ids);
        }
        value.push_back(faces);
    }
    return value.dump();
}

nb::dict Preview(const Model& m) {
    nb::dict out;
    out["kind"] = m.kind;
    nb::dict curves;
    for (size_t i = 0; i < m.curves.size(); ++i) {
        nb::dict d;
        d["knots"] = KnotsOf(m.curves[i].second).size();
        PutQa(d, m.reports[i]);
        curves[m.curves[i].first.c_str()] = d;
    }
    out["curves"] = curves;
    out["alignment"] = ToPython(m.alignment);
    nb::list notes;
    nb::dict counts;
    if (m.kind == "curve") {
        counts["knots"] = KnotsOf(m.curve).size();
        counts["splines"] = 1;
    } else {
        notes.append("Quad cage; surface intersections and thickness are not certified. Inspect the silhouette and highlights.");
        notes.append("Sampling resolution and loft seam correspondence remain fixed during parameter updates.");
        counts["vertices"] = m.vertices.size();
        counts["faces"] = m.faces.size();
        V3 lo = m.vertices[0], hi = m.vertices[0];
        for (const V3& p : m.vertices) {
            lo = {std::min(lo.x, p.x), std::min(lo.y, p.y), std::min(lo.z, p.z)};
            hi = {std::max(hi.x, p.x), std::max(hi.y, p.y), std::max(hi.z, p.z)};
        }
        out["bounds"] = nb::make_tuple(Tuple(lo), Tuple(hi));
    }
    out["notes"] = notes;
    out["counts"] = counts;
    return out;
}

// ── Editable splines in the scene ────────────────────────────────

const char* const kKnotTypes[] = {"smooth", "corner", "bezier", "bezier_corner"};      // by KTYPE_*
const char* const kMxsKnotTypes[] = {"smooth", "corner", "bezier", "bezierCorner"};    // MAXScript's names

int KnotCode(int ktype) { return ktype & 3; }
bool KeepsHandles(int ktype) { return KnotCode(ktype) == KTYPE_BEZIER || KnotCode(ktype) == KTYPE_BEZIER_CORNER; }

// corner, smooth, bezier, bezier_corner (also bezierCorner) and, when
// creating, bounded.
std::string KnotTypeName(const std::string& name, bool creating) {
    std::string key = Lower(name);
    key.erase(std::remove(key.begin(), key.end(), '_'), key.end());
    if (key == "beziercorner") return "bezier_corner";
    if (key == "corner" || key == "smooth" || key == "bezier" || (creating && key == "bounded")) return key;
    std::vector<std::string> names{"corner", "smooth", "bezier", "bezier_corner"};
    if (creating) names.push_back("bounded");
    throw BadParam("unknown knot type '" + name + "'; use " + Join(names) + "." + Suggest(name, names));
}

int KnotTypeCode(const std::string& canonical) {
    for (int i = 0; i < 4; ++i)
        if (canonical == kKnotTypes[i]) return i;
    throw BadParam("bounded knots exist only while creating a spline; use bezier_corner");
}

int LineTypeCode(const std::string& name) {
    const std::string key = Lower(name);
    if (key == "curve") return LTYPE_CURVE;
    if (key == "line") return LTYPE_LINE;
    throw BadParam("unknown segment type '" + name + "'; use line or curve");
}

const char* LineTypeName(int ltype) { return ltype == LTYPE_LINE ? "line" : "curve"; }

bool IsEditableSpline(Object* base) {
    return base && base->SuperClassID() == SHAPE_CLASS_ID &&
           (base->ClassID() == splineShapeClassID || base->ClassID() == Class_ID(SPLINE3D_CLASS_ID, 0));
}

SplineShape* EditableSpline(INode* node) {
    Object* base = BaseObject(node);
    if (IsEditableSpline(base)) return static_cast<SplineShape*>(base);
    if (base && base->SuperClassID() == SHAPE_CLASS_ID)
        throw BadParam(Quoted(node) + " is a parametric " + ClassOf(node) +
                       "; node.convert_to_spline() makes its knots editable (collapsing its stack)");
    throw BadParam(Quoted(node) + " is " + WithArticle(ClassOf(node)) + ", not a spline shape");
}

Spline3D* SplineAt(SplineShape* ss, int index, INode* node) {
    const int count = ss->shape.SplineCount();
    if (index < 0 || index >= count)
        throw NotFound(Quoted(node) + " has no spline " + std::to_string(index) + " (it has " + std::to_string(count) +
                       "); read node.splines again");
    return ss->shape.GetSpline(index);
}

// Python-style index (negative counts from the end) into count items.
int ItemIndex(int index, int count, const char* what) {
    const int i = index < 0 ? index + count : index;
    if (i < 0 || i >= count)
        throw BadParam(std::string(what) + " " + std::to_string(index) + " is out of range: the spline has " +
                       std::to_string(count) + " " + what + (count == 1 ? "" : "s"));
    return i;
}

Matrix3 WorldTM(INode* node) { return node->GetObjectTM(Now()); }

Matrix3 LocalTM(INode* node, const Matrix3& world) {
    if (std::abs(DotProd(CrossProd(world.GetRow(0), world.GetRow(1)), world.GetRow(2))) < 1e-12f)
        throw MaxError(Quoted(node) + " has a singular transform (a zero scale); fix its scale before editing knots");
    return Inverse(world);
}

// Equivalent of MAXScript's updateShape: handles, selections, binds, caches.
void Commit(SplineShape* ss) {
    BezierShape& shape = ss->shape;
    for (int i = 0; i < shape.SplineCount(); ++i) shape.GetSpline(i)->ComputeBezPoints();
    shape.UpdateSels(TRUE);
    shape.UpdateBindList();
    ss->InvalidateGeomCache();
    ss->NotifyDependents(FOREVER, PART_GEOM | PART_TOPO | PART_SELECT, REFMSG_CHANGE);
}

std::string CurveToken(INode* node, SplineShape* ss) {
    std::string bytes;
    auto put = [&](const auto& value) { bytes.append(reinterpret_cast<const char*>(&value), sizeof value); };
    auto point = [&](const Point3& p) { put(p.x); put(p.y); put(p.z); };
    put((unsigned long long)Animatable::GetHandleByAnim(node));
    BezierShape& shape = ss->shape;
    put(shape.SplineCount());
    for (int s = 0; s < shape.SplineCount(); ++s) {
        Spline3D* sp = shape.GetSpline(s);
        put(sp->Closed());
        put(sp->KnotCount());
        for (int k = 0; k < sp->KnotCount(); ++k) {
            put(sp->GetKnotType(k));
            put(sp->GetLineType(k));
            point(sp->GetKnotPoint(k));
            point(sp->GetInVec(k));
            point(sp->GetOutVec(k));
        }
    }
    const Matrix3 tm = WorldTM(node);
    for (int row = 0; row < 4; ++row) point(tm.GetRow(row));
    return HexDigest(Sha256(bytes)).substr(0, 32);
}

// An editable spline about to change, after the guards every edit shares.
struct Edit {
    INode* node = nullptr;
    SplineShape* shape = nullptr;
    Matrix3 world;  // object space to world
    Matrix3 local;  // world to object space
};

Edit OpenEdit(AnimHandle handle, const std::optional<std::string>& expected) {
    Edit e;
    e.node = Resolve(Node{handle});
    e.shape = EditableSpline(e.node);
    if (expected && *expected != CurveToken(e.node, e.shape))
        throw MaxError("STALE_CURVE: " + Quoted(e.node) +
                       " changed since that curve_token; read node.curve_token and the knots again");
    if (InstanceCount(e.node) > 1)
        throw MaxError(Quoted(e.node) + " shares its spline with other nodes (instances); make it unique before editing");
    for (int i = 0; i < e.shape->cont.Count(); ++i)
        if (e.shape->cont[i])
            throw MaxError(Quoted(e.node) + " has animated knots (vertex controllers), which would override knot edits");
    e.world = WorldTM(e.node);
    e.local = LocalTM(e.node, e.world);
    return e;
}

// As MAXScript's updateShape does, a shape open in the Modify panel is
// deselected first: the panel does not support scripted changes.
void LeaveModifyPanel(INode* node, SplineShape* shape) {
    Interface* ip = GetCOREInterface();
    if (ip->GetCommandPanelTaskMode() == TASK_MODE_MODIFY && ip->GetCurEditObject() == shape) ip->DeSelectNode(node);
}

struct SplineView {
    INode* node = nullptr;
    SplineShape* shape = nullptr;
    Spline3D* spline = nullptr;
    Matrix3 world;
};

struct SplineRef {
    AnimHandle node = 0;
    int index = 0;
};

SplineView ViewOf(const SplineRef& r) {
    SplineView v;
    v.node = Resolve(Node{r.node});
    v.shape = EditableSpline(v.node);
    v.spline = SplineAt(v.shape, r.index, v.node);
    v.world = WorldTM(v.node);
    return v;
}

// ── Knots ────────────────────────────────────────────────────────

struct Knot {
    Point3 pos{0, 0, 0};
    std::optional<std::string> type;
    std::optional<Point3> in, out;
    std::optional<std::string> seg;
};

Knot ReadKnot(Spline3D* sp, int k, const Matrix3& world) {
    Knot out;
    out.pos = sp->GetKnotPoint(k) * world;
    out.type = kKnotTypes[KnotCode(sp->GetKnotType(k))];
    out.in = sp->GetInVec(k) * world;
    out.out = sp->GetOutVec(k) * world;
    if (k < sp->Segments()) out.seg = LineTypeName(sp->GetLineType(k));
    return out;
}

std::vector<Knot> ReadKnots(const SplineRef& r) {
    const SplineView v = ViewOf(r);
    std::vector<Knot> out;
    for (int k = 0; k < v.spline->KnotCount(); ++k) out.push_back(ReadKnot(v.spline, k, v.world));
    return out;
}

std::optional<Point3> OptionalPoint(nb::handle value, const std::string& what) {
    if (value.is_none()) return std::nullopt;
    return ToPoint3(value, what);
}

nb::object OptionalTuple(const std::optional<Point3>& p) { return p ? FromPoint3(*p) : nb::none(); }

std::string Text(nb::handle value, const std::string& what) {
    if (!nb::isinstance<nb::str>(value)) throw BadParam(what + " must be a string, not " + TypeNameOf(value));
    return nb::cast<std::string>(value);
}

std::optional<std::string> OptionalKnotType(const std::optional<std::string>& name) {
    if (!name) return std::nullopt;
    return KnotTypeName(*name, true);
}

std::optional<std::string> OptionalSegment(const std::optional<std::string>& name) {
    if (!name) return std::nullopt;
    return std::string(LineTypeName(LineTypeCode(*name)));
}

// A Knot, a dict of Knot fields, or a point.
Knot KnotFrom(nb::handle item, const std::string& what) {
    if (nb::isinstance<Knot>(item)) return nb::cast<Knot>(item);
    Knot k;
    if (!nb::isinstance<nb::dict>(item)) {
        k.pos = ToPoint3(item, what);
        return k;
    }
    const std::vector<std::string> keys{"pos", "type", "in_vec", "out_vec", "seg"};
    bool hasPos = false;
    for (auto [key, value] : nb::borrow<nb::dict>(item)) {
        const std::string name = Text(key, what + " key");
        const std::string field = what + "." + name;
        if (name == "pos") { k.pos = ToPoint3(value, field); hasPos = true; }
        else if (name == "type") k.type = value.is_none() ? std::nullopt : OptionalKnotType(Text(value, field));
        else if (name == "in_vec") k.in = OptionalPoint(value, field);
        else if (name == "out_vec") k.out = OptionalPoint(value, field);
        else if (name == "seg") k.seg = value.is_none() ? std::nullopt : OptionalSegment(Text(value, field));
        else throw BadParam(what + ": unknown key '" + name + "'." + Suggest(name, keys) + " Keys: " + Join(keys));
    }
    if (!hasPos) throw BadParam(what + " needs 'pos'");
    return k;
}

std::vector<nb::handle> Items(nb::handle value, const char* what) {
    if (!nb::isinstance<nb::list>(value) && !nb::isinstance<nb::tuple>(value))
        throw BadParam(std::string(what) + " must be a list, not " + TypeNameOf(value));
    std::vector<nb::handle> items;
    for (nb::handle item : value) items.push_back(item);
    return items;
}

// A knot to create, in world space, with bounded already resolved.
struct NewKnot {
    V3 pos;
    std::string type;
    std::optional<V3> in, out;
    int line = LTYPE_CURVE;
};

// Explicit short handles: the unit-chord bisector, at most a third of the
// shorter adjacent chord, so segments never travel backwards along their
// chords. Reversing or coincident neighbours get zero handles.
std::vector<NewKnot> ResolveBounded(const std::vector<NewKnot>& source, bool closed) {
    std::vector<NewKnot> knots = source;
    const size_t n = source.size();
    for (size_t i = 0; i < n; ++i) {
        if (source[i].type != "bounded") continue;
        const V3 p = source[i].pos;
        const bool before = i > 0 || closed, after = i + 1 < n || closed;
        std::vector<V3> chords;
        if (before) chords.push_back(p - source[(i + n - 1) % n].pos);
        if (after) chords.push_back(source[(i + 1) % n].pos - p);
        double shortest = std::numeric_limits<double>::infinity();
        for (const V3& c : chords) shortest = std::min(shortest, Len(c));
        V3 direction;
        if (!chords.empty() && shortest > 1e-8) {
            V3 tangent;
            for (const V3& c : chords) tangent = tangent + V3{c.x / Len(c), c.y / Len(c), c.z / Len(c)};
            const double magnitude = Len(tangent);
            if (magnitude > 1e-8) direction = tangent * (shortest / (3 * magnitude));
        }
        knots[i].type = "bezier_corner";
        knots[i].in = before ? p - direction : p;
        knots[i].out = after ? p + direction : p;
    }
    return knots;
}

// Smooth knots between unevenly spaced neighbours, whose automatic tangents can overshoot.
std::vector<int> OvershootRisk(const std::vector<NewKnot>& knots, bool closed) {
    std::vector<int> risky;
    const size_t n = knots.size();
    for (size_t i = 0; i < n; ++i) {
        if (knots[i].type != "smooth" || (!closed && (i == 0 || i + 1 == n))) continue;
        const double a = Len(knots[i].pos - knots[(i + n - 1) % n].pos), b = Len(knots[(i + 1) % n].pos - knots[i].pos);
        if (std::min(a, b) <= 1e-8 || std::max(a, b) >= 4 * std::min(a, b)) risky.push_back(int(i));
    }
    return risky;
}

void WarnOvershoot(const std::vector<int>& risky) {
    if (risky.empty()) return;
    std::vector<std::string> ids;
    for (int i : risky) ids.push_back(std::to_string(i));
    const std::string message = "smooth knots " + Join(ids) +
        " sit between unevenly spaced points and can overshoot; use knot='bounded' or explicit Bezier handles";
    if (PyErr_WarnEx(PyExc_UserWarning, message.c_str(), 1) < 0) throw nb::python_error();
}

struct NewSpline {
    std::vector<NewKnot> knots;
    std::vector<int> risky;
};

NewSpline ParsePoints(nb::handle points, const std::string& knot, bool closed) {
    const std::string fallback = KnotTypeName(knot, true);
    const std::vector<nb::handle> items = Items(points, "points");
    if (items.size() < 2) throw BadParam("a spline needs at least 2 points");
    std::vector<NewKnot> knots;
    for (size_t i = 0; i < items.size(); ++i) {
        const std::string what = "points[" + std::to_string(i) + "]";
        const Knot k = KnotFrom(items[i], what);
        NewKnot n{FromP3(k.pos), k.type.value_or(fallback)};
        if (n.type == "bezier" || n.type == "bezier_corner") {
            if (!k.in || !k.out)
                throw BadParam(what + ": bezier knots need in_vec and out_vec (absolute handle positions); "
                                      "use 'smooth' for automatic handles");
            n.in = FromP3(*k.in);
            n.out = FromP3(*k.out);
        } else if (n.type == "bounded" && (k.in || k.out)) {
            throw BadParam(what + ": bounded knots compute their own handles; use 'bezier_corner' for explicit ones");
        }
        n.line = k.seg ? LineTypeCode(*k.seg) : n.type == "corner" ? LTYPE_LINE : LTYPE_CURVE;
        knots.push_back(n);
    }
    return {ResolveBounded(knots, closed), OvershootRisk(knots, closed)};
}

void FillSpline(Spline3D* sp, const std::vector<NewKnot>& knots, bool closed, const Matrix3& local) {
    for (const NewKnot& k : knots) {
        const Point3 p = ToP3(k.pos) * local;
        const Point3 in = k.in ? ToP3(*k.in) * local : p, out = k.out ? ToP3(*k.out) * local : p;
        sp->AddKnot(SplineKnot(KnotTypeCode(k.type), k.line, p, in, out));
    }
    if (closed) sp->SetClosed();
}

// One knot change in object space; unset fields keep their value.
struct KnotEdit {
    int index = 0;
    std::optional<int> type, line;
    std::optional<Point3> pos, in, out;
};

void CheckHandles(Spline3D* sp, const KnotEdit& e) {
    if (!e.in && !e.out) return;
    const int type = e.type.value_or(sp->GetKnotType(e.index));
    if (KeepsHandles(type)) return;
    const std::string knot = "knot " + std::to_string(e.index);
    if (e.type)
        throw BadParam(knot + ": handles need type 'bezier' or 'bezier_corner', not '" + kKnotTypes[KnotCode(type)] + "'");
    throw BadParam(knot + " is " + kKnotTypes[KnotCode(type)] + "; pass type='bezier_corner' to set its handles");
}

// A moved knot carries its handles unless new ones are given.
void Apply(Spline3D* sp, const KnotEdit& e) {
    const int k = e.index;
    const Point3 oldPos = sp->GetKnotPoint(k), oldIn = sp->GetInVec(k), oldOut = sp->GetOutVec(k);
    if (e.type) sp->SetKnotType(k, *e.type);
    if (e.pos) sp->SetKnotPoint(k, *e.pos);
    if (KeepsHandles(sp->GetKnotType(k))) {
        const Point3 delta = e.pos ? *e.pos - oldPos : Point3(0, 0, 0);
        sp->SetInVec(k, e.in ? *e.in : e.pos ? oldIn + delta : oldIn);
        sp->SetOutVec(k, e.out ? *e.out : e.pos ? oldOut + delta : oldOut);
    }
    if (e.line) sp->SetLineType(k, *e.line);
}

void SetKnot(const SplineRef& r, int index, PointOrNone pos, PointOrNone in, PointOrNone out,
             const std::optional<std::string>& type, const std::optional<std::string>& seg,
             const std::optional<std::string>& expected) {
    if (pos.is_none() && in.is_none() && out.is_none() && !type && !seg)
        throw BadParam("set_knot needs pos, in_vec, out_vec, type or seg");
    const Edit e = OpenEdit(r.node, expected);
    Spline3D* sp = SplineAt(e.shape, r.index, e.node);
    KnotEdit edit;
    edit.index = ItemIndex(index, sp->KnotCount(), "knot");
    if (type) edit.type = KnotTypeCode(KnotTypeName(*type, false));
    if (seg) edit.line = LineTypeCode(*seg);
    if (auto p = OptionalPoint(pos, "pos")) edit.pos = *p * e.local;
    if (auto p = OptionalPoint(in, "in_vec")) edit.in = *p * e.local;
    if (auto p = OptionalPoint(out, "out_vec")) edit.out = *p * e.local;
    CheckHandles(sp, edit);
    LeaveModifyPanel(e.node, e.shape);
    Apply(sp, edit);
    Commit(e.shape);
}

// Only what differs from the current knot is written, so untouched knots
// keep their exact coordinates.
KnotEdit Difference(Spline3D* sp, int k, const Knot& want, const Edit& e) {
    const Knot now = ReadKnot(sp, k, e.world);
    KnotEdit edit;
    edit.index = k;
    if (want.type) {
        const std::string type = KnotTypeName(*want.type, false);
        if (type != *now.type) edit.type = KnotTypeCode(type);
    }
    if (!(want.pos == now.pos)) edit.pos = want.pos * e.local;
    if (want.in && !(*want.in == *now.in)) edit.in = *want.in * e.local;
    if (want.out && !(*want.out == *now.out)) edit.out = *want.out * e.local;
    if (want.seg && want.seg != now.seg) edit.line = LineTypeCode(*want.seg);
    return edit;
}

void SetKnots(const SplineRef& r, KnotsArg knots, const std::optional<std::string>& expected) {
    const Edit e = OpenEdit(r.node, expected);
    Spline3D* sp = SplineAt(e.shape, r.index, e.node);
    const std::vector<nb::handle> items = Items(knots.value, "knots");
    const int count = sp->KnotCount();
    if (int(items.size()) != count)
        throw BadParam("set_knots needs one entry per knot: the spline has " + std::to_string(count) + ", got " +
                       std::to_string(items.size()) + "; insert() and delete_knot() change the count");
    std::vector<KnotEdit> edits;
    for (int k = 0; k < count; ++k) {
        edits.push_back(Difference(sp, k, KnotFrom(items[k], "knots[" + std::to_string(k) + "]"), e));
        CheckHandles(sp, edits.back());
    }
    LeaveModifyPanel(e.node, e.shape);
    for (const KnotEdit& edit : edits) Apply(sp, edit);
    Commit(e.shape);
}

// A topology edit after the shared guards: `check` refuses before anything
// changes, `change` writes.
void EditSpline(const SplineRef& r, const std::optional<std::string>& expected,
                const std::function<void(Spline3D*)>& check,
                const std::function<void(BezierShape&, Spline3D*)>& change) {
    const Edit e = OpenEdit(r.node, expected);
    Spline3D* sp = SplineAt(e.shape, r.index, e.node);
    check(sp);
    LeaveModifyPanel(e.node, e.shape);
    change(e.shape->shape, sp);
    Commit(e.shape);
}

int Insert(const SplineRef& r, int segment, double param, const std::optional<std::string>& expected) {
    if (!(param > 0 && param < 1)) throw BadParam("param must be strictly between 0 and 1");
    int s = 0;
    EditSpline(r, expected, [&](Spline3D* sp) {
        if (sp->Segments() == 0) throw BadParam("the spline has no segments to insert into");
        s = ItemIndex(segment, sp->Segments(), "segment");
    }, [&](BezierShape&, Spline3D* sp) { sp->RefineSegment(s, float(param), SPLINE_INTERP_SIMPLE); });
    return s + 1;
}

void DeleteKnot(const SplineRef& r, int index, const std::optional<std::string>& expected) {
    int k = 0;
    EditSpline(r, expected, [&](Spline3D* sp) {
        k = ItemIndex(index, sp->KnotCount(), "knot");
        if (sp->KnotCount() <= (sp->Closed() ? 3 : 2))
            throw BadParam("deleting a knot would leave too few (an open spline needs 2, a closed one 3); "
                           "delete() removes the whole spline");
    }, [&](BezierShape&, Spline3D* sp) { sp->DeleteKnot(k); });
}

void NoCheck(Spline3D*) {}

// ── Segments, sampling and viewport targeting ────────────────────

Curve WorldCurve(Spline3D* sp, const Matrix3& world) {
    Curve c;
    c.closed = sp->Closed() != 0;
    const int n = sp->KnotCount();
    auto at = [&](const Point3& p) { return FromP3(p * world); };
    for (int k = 0; k < sp->Segments(); ++k) {
        const int j = (k + 1) % n;
        const V3 a = at(sp->GetKnotPoint(k)), b = at(sp->GetKnotPoint(j));
        if (sp->GetLineType(k) == LTYPE_LINE) c.segments.push_back(Chord(a, b));
        else c.segments.push_back({a, at(sp->GetOutVec(k)), at(sp->GetInVec(j)), b});
    }
    return c;
}

struct Segment {
    std::string type;
    double length = 0;
    std::array<V3, 4> points;
};

std::vector<Segment> Segments(const SplineRef& r) {
    const SplineView v = ViewOf(r);
    std::vector<Segment> out;
    for (const Cubic& c : WorldCurve(v.spline, v.world).segments)
        out.push_back({c.straight ? "line" : "curve", CubicLength(c), {c.start, c.out, c.in, c.end}});
    return out;
}

double SplineLength(const SplineRef& r) {
    const SplineView v = ViewOf(r);
    double total = 0;
    for (const Cubic& c : WorldCurve(v.spline, v.world).segments) total += CubicLength(c);
    return total;
}

std::vector<nb::object> Sample(const SplineRef& r, int count) {
    const SplineView v = ViewOf(r);
    const Curve c = WorldCurve(v.spline, v.world);
    if (c.segments.empty()) throw BadParam("the spline has no segments to sample");
    std::vector<V3> controls;
    for (const Cubic& s : c.segments) controls.insert(controls.end(), {s.start, s.out, s.in, s.end});
    V3 lo = controls[0], hi = controls[0];
    for (const V3& p : controls) {
        lo = {std::min(lo.x, p.x), std::min(lo.y, p.y), std::min(lo.z, p.z)};
        hi = {std::max(hi.x, p.x), std::max(hi.y, p.y), std::max(hi.z, p.z)};
    }
    const double tolerance = std::max(Len(hi - lo) * 1e-5, 1e-9);
    std::vector<nb::object> out;
    for (const V3& p : Samples(c, count, tolerance, "count", std::numeric_limits<size_t>::max())) out.push_back(Tuple(p));
    return out;
}

nb::dict SplineQa(const SplineRef& r, double tolerance) {
    if (!(1e-6 <= tolerance && tolerance <= 1e6)) throw BadParam("tolerance must be between 1e-6 and 1e6 scene units");
    const SplineView v = ViewOf(r);
    const Curve c = WorldCurve(v.spline, v.world);
    nb::dict out;
    if (c.segments.empty()) {
        out["complete"] = false;
        out["reason"] = "the spline has no segments";
        return out;
    }
    try {
        PutQa(out, CurveQa(c, tolerance));
    } catch (const BadParam& error) {
        out["complete"] = false;
        out["reason"] = error.what();
        out["segment_backtracking"] = BacktrackList(Backtracking(c));
    }
    return out;
}

struct Target {
    int knot = 0;
    const char* component = "knot";  // knot, in_handle or out_handle
    Point3 point;
    std::string type;
};

// Knots, and the handles of knots that have them, in knot order.
std::vector<Target> Targets(const SplineView& v, const std::optional<std::vector<int>>& knots) {
    const int count = v.spline->KnotCount();
    std::vector<int> ids;
    if (knots) {
        for (int k : *knots) ids.push_back(ItemIndex(k, count, "knot"));
        std::sort(ids.begin(), ids.end());
        ids.erase(std::unique(ids.begin(), ids.end()), ids.end());
    } else {
        for (int k = 0; k < count; ++k) ids.push_back(k);
    }
    std::vector<Target> out;
    for (int k : ids) {
        const Knot knot = ReadKnot(v.spline, k, v.world);
        out.push_back({k, "knot", knot.pos, *knot.type});
        if (*knot.type == "corner") continue;
        out.push_back({k, "in_handle", *knot.in, *knot.type});
        out.push_back({k, "out_handle", *knot.out, *knot.type});
    }
    return out;
}

json PointJson(const Point3& p) { return json::array({p.x, p.y, p.z}); }

// The viewport calls redraw and can pump messages: the shape must not change meanwhile.
void RequireSameCurve(const SplineView& v, const std::string& before) {
    if (CurveToken(v.node, EditableSpline(v.node)) != before)
        throw MaxError("STALE_CURVE: " + Quoted(v.node) + " changed during the viewport call; read its knots again");
}

nb::dict Capture(const SplineRef& r, const std::optional<std::vector<int>>& knots) {
    const SplineView v = ViewOf(r);
    const std::string token = CurveToken(v.node, v.shape);
    const std::vector<Target> targets = Targets(v, knots);
    json labels = json::array();
    for (const Target& t : targets) {
        if (labels.size() >= 100) break;
        const char* prefix = std::strcmp(t.component, "knot") == 0 ? "K" : std::strcmp(t.component, "in_handle") == 0 ? "I" : "O";
        labels.push_back({{"point", PointJson(t.point)}, {"text", prefix + std::to_string(t.knot)}});
    }
    const size_t shown = labels.size();
    const json reply = NativeJson("native:agent_viewport", {{"action", "capture"}, {"source", "agent"}, {"labels", labels}});
    RequireSameCurve(v, token);
    // Same shape as Poly.capture: pick() takes 'view'.
    nb::dict out;
    out["file"] = ToPython(reply.value("file", json()));
    out["view"] = ToPython(reply.value("agent_viewport", json::object()).value("view_token", json()));
    out["width"] = ToPython(reply.value("width", json()));
    out["height"] = ToPython(reply.value("height", json()));
    out["labels"] = shown;
    out["labels_truncated"] = targets.size() > shown;
    return out;
}

// One dict per knot or handle within tolerance: {knot, component, point, type, distance, image}.
using SplinePicks = nb::typed<nb::list, nb::typed<nb::dict, std::string, SplinePickField>>;

SplinePicks Pick(const SplineRef& r, double x, double y, const std::string& view, double tolerance,
                 const std::optional<std::vector<int>>& knots) {
    if (!(0 <= x && x <= 1 && 0 <= y && y <= 1)) throw BadParam("x and y are fractions of the captured image, 0-1");
    if (!(0 <= tolerance && tolerance <= .25)) throw BadParam("tolerance must be 0-0.25 of the image's shorter side");
    if (view.empty()) throw BadParam("view needs the view_token of the capture you picked in");
    const SplineView v = ViewOf(r);
    const std::string token = CurveToken(v.node, v.shape);
    const std::vector<Target> targets = Targets(v, knots);
    struct Hit { const Target* target; double distance; double u, w; };
    std::vector<Hit> hits;
    int width = 0, height = 0;
    // 1000 points per projection; even without targets one call checks the view.
    for (size_t first = 0;; first += 1000) {
        const size_t last = std::min(targets.size(), first + 1000);
        json points = json::array();
        for (size_t i = first; i < last; ++i) points.push_back(PointJson(targets[i].point));
        const json reply = NativeJson("native:agent_viewport", {{"action", "project"}, {"points", points}, {"expected_view", view}});
        if (reply.value("view_token", json()) != json(view)) throw MaxError("STALE_VIEW: the agent view changed; capture again");
        const json w = reply.value("width", json()), h = reply.value("height", json());
        if (!IsNumber(w) || !IsNumber(h) || w.get<int>() < 2 || h.get<int>() < 2) throw MaxError("the agent viewport reported no size");
        if (width && (w.get<int>() != width || h.get<int>() != height)) throw MaxError("STALE_VIEW: the image size changed; capture again");
        width = w.get<int>();
        height = h.get<int>();
        const json pixels = reply.value("pixels", json::array()), projections = reply.value("projections", json::array());
        if (pixels.size() != last - first) throw MaxError("the agent viewport projected only some knots; capture again");
        for (size_t i = 0; i < pixels.size(); ++i) {
            const bool behind = projections.size() == pixels.size() && projections[i].value("in_front", true) == false;
            if (pixels[i].type() != json::value_t::array || pixels[i].size() != 2 || behind) continue;
            const double px = pixels[i][0].get<double>(), py = pixels[i][1].get<double>();
            const double distance = std::hypot(px - x * (width - 1), py - y * (height - 1));
            if (distance <= tolerance * std::min(width, height))
                hits.push_back({&targets[first + i], distance, px / (width - 1), py / (height - 1)});
        }
        if (last == targets.size()) break;
    }
    RequireSameCurve(v, token);
    std::sort(hits.begin(), hits.end(), [](const Hit& a, const Hit& b) {
        if (a.distance != b.distance) return a.distance < b.distance;
        if (a.target->knot != b.target->knot) return a.target->knot < b.target->knot;
        return std::strcmp(a.target->component, b.target->component) < 0;
    });
    nb::list out;
    for (const Hit& hit : hits) {
        nb::dict row;
        row["knot"] = hit.target->knot;
        row["component"] = hit.target->component;
        row["point"] = FromPoint3(hit.target->point);
        row["type"] = hit.target->type;
        row["distance"] = hit.distance;
        row["image"] = nb::make_tuple(hit.u, hit.w);
        out.append(row);
    }
    return out;
}

// ── Creating shapes and cages ────────────────────────────────────

INode* PlaceNode(Object* object, const std::string& name, const MCHAR* fallback, const V3& origin) {
    Interface* ip = GetCOREInterface();
    INode* node = ip->CreateObjectNode(object);
    if (!node) throw MaxError("3ds Max could not create a node");
    MSTR nodeName = name.empty() ? MSTR(fallback) : MSTR(Utf8ToWide(name).c_str());
    if (name.empty()) ip->MakeNameUnique(nodeName);
    node->SetName(nodeName.data());
    NoAnimate noAnimate;
    Matrix3 tm = TransMatrix(ToP3(origin));
    node->SetNodeTM(Now(), tm);
    return node;
}

SplineShape* NewSplineShape() {
    auto* created = static_cast<Animatable*>(GetCOREInterface()->CreateInstance(SHAPE_CLASS_ID, splineShapeClassID));
    if (!created || created->ClassID() != splineShapeClassID) {
        FreeUnused(created);
        throw MaxError("3ds Max could not create an editable spline");
    }
    return static_cast<SplineShape*>(created);
}

Node CreateSpline(KnotsArg points, const std::string& name, bool closed, const std::string& knot, bool centerPivot) {
    RequireMain();
    const NewSpline parsed = ParsePoints(points.value, knot, closed);
    WarnOvershoot(parsed.risky);
    std::vector<V3> positions;
    for (const NewKnot& k : parsed.knots) positions.push_back(k.pos);
    const V3 origin = centerPivot ? Center(positions) : V3{};
    SplineShape* ss = NewSplineShape();
    FillSpline(ss->shape.NewSpline(), parsed.knots, closed, TransMatrix(-ToP3(origin)));
    Commit(ss);
    return Wrap(PlaceNode(ss, name, L"Shape", origin));
}

SplineRef AddSpline(const Node& n, KnotsArg points, bool closed, const std::string& knot,
                    const std::optional<std::string>& expected) {
    const NewSpline parsed = ParsePoints(points.value, knot, closed);
    const Edit e = OpenEdit(n.handle, expected);
    WarnOvershoot(parsed.risky);
    LeaveModifyPanel(e.node, e.shape);
    FillSpline(e.shape->shape.NewSpline(), parsed.knots, closed, e.local);
    Commit(e.shape);
    return {n.handle, e.shape->shape.SplineCount() - 1};
}

INode* CreateCurveNode(const std::string& name, const Curve& curve, const V3& origin) {
    SplineShape* ss = NewSplineShape();
    Spline3D* sp = ss->shape.NewSpline();
    for (const CurveKnot& k : KnotsOf(curve))
        sp->AddKnot(SplineKnot(KTYPE_BEZIER_CORNER, k.line ? LTYPE_LINE : LTYPE_CURVE, ToP3(k.pos - origin),
                               ToP3(k.in - origin), ToP3(k.out - origin)));
    if (curve.closed) sp->SetClosed();
    Commit(ss);
    return PlaceNode(ss, name, L"Curve", origin);
}

MNMesh* PolyMesh(Object* base, EPoly** poly = nullptr) {
    if (!base || base->SuperClassID() != GEOMOBJECT_CLASS_ID || base->ClassID() != EPOLYOBJ_CLASS_ID) return nullptr;
    auto* epoly = static_cast<EPoly*>(base->GetInterface(EPOLY_INTERFACE));
    if (poly) *poly = epoly;
    return epoly ? epoly->GetMeshPtr() : nullptr;
}

// An Editable Poly built face by face, as polyop.createPolygon does, so
// illegal topology is refused instead of stored.
INode* CreateMeshNode(const std::string& name, const Model& m, const V3& origin) {
    auto* object = static_cast<Object*>(GetCOREInterface()->CreateInstance(GEOMOBJECT_CLASS_ID, EPOLYOBJ_CLASS_ID));
    EPoly* poly = nullptr;
    MNMesh* mesh = PolyMesh(object, &poly);
    if (!mesh) {
        FreeUnused(object);
        throw MaxError("3ds Max could not create an Editable Poly");
    }
    for (const V3& v : m.vertices) mesh->NewVert(ToP3(v - origin));
    mesh->FillInMesh();
    for (size_t i = 0; i < m.faces.size(); ++i) {
        std::vector<int> ids = m.faces[i];
        if (mesh->CreateFace(int(ids.size()), ids.data()) < 0) {
            FreeUnused(object);
            throw MaxError("could not create polygon " + std::to_string(i) + "; check section winding and topology");
        }
    }
    poly->LocalDataChanged(GEOM_CHANNEL | TOPO_CHANNEL);
    const char* kind = m.kind == "sweep" ? "Sweep" : "Loft";
    return PlaceNode(object, name, Utf8ToWide(kind).c_str(), origin);
}

// ── curve_model recipes ──────────────────────────────────────────

constexpr int kRecipeSlot = 1129731140;  // the tool's AppData id ("CVMD")
constexpr const char* kSchema = "3dsmax-mcp/curve-model";
constexpr size_t kMaxRecipeBytes = 4000000;
constexpr size_t kHistoryLimit = 16;

std::string McPoint(const Point3& p) { return "[" + G9(p.x) + "," + G9(p.y) + "," + G9(p.z) + "]"; }

// The text the tool's cvToken hashes for the base spline.
std::string SplineText(SplineShape* ss) {
    BezierShape& shape = ss->shape;
    std::string out = std::to_string(shape.SplineCount()) + "|";
    for (int s = 0; s < shape.SplineCount(); ++s) {
        Spline3D* sp = shape.GetSpline(s);
        out += std::string(sp->Closed() ? "true" : "false") + "|" + std::to_string(sp->KnotCount()) + "|";
        for (int k = 0; k < sp->KnotCount(); ++k) {
            out += std::string(kMxsKnotTypes[KnotCode(sp->GetKnotType(k))]) + "|" + McPoint(sp->GetKnotPoint(k)) + "|" +
                   McPoint(sp->GetInVec(k)) + "|" + McPoint(sp->GetOutVec(k)) + "|";
            if (k < sp->Segments()) out += std::string(LineTypeName(sp->GetLineType(k))) + "|";
        }
    }
    return out;
}

// The tool's cmToken: the geometry state recipes record. None when the base
// is neither an editable spline nor an Editable Poly.
std::optional<std::string> Fingerprint(INode* node) {
    Object* base = BaseObject(node);
    if (IsEditableSpline(base)) return DashedDigest(Sha256(SplineText(static_cast<SplineShape*>(base))));
    if (MNMesh* mesh = PolyMesh(base)) return CageFingerprint(*mesh);
    return std::nullopt;
}

struct Revision {
    Values parameters;
    std::string fingerprint;
};

struct Recipe {
    json definition;
    V3 origin;
    std::string kind;
    json alignment;
    std::string topology;  // SHA-256 hex of TopologyText
    std::vector<Revision> history;
};

json ValuesJson(const Values& values) {
    json out = json::object();
    for (const auto& [key, value] : values) out[key] = value;
    return out;
}

nb::dict ValuesDict(const Values& values) {
    nb::dict out;
    for (const auto& [key, value] : values) out[key.c_str()] = value;
    return out;
}

std::string Encode(const Recipe& r) {
    json history = json::array();
    for (const Revision& row : r.history)
        history.push_back({{"parameters", ValuesJson(row.parameters)}, {"fingerprint", row.fingerprint}});
    const json data = {{"schema", kSchema}, {"version", 1}, {"definition", r.definition},
                       {"origin", json::array({r.origin.x, r.origin.y, r.origin.z})}, {"kind", r.kind},
                       {"alignment", r.alignment}, {"topology", r.topology}, {"history", history}};
    const std::string text = data.dump(-1, ' ', true);
    if (text.size() > kMaxRecipeBytes) throw BadParam("Curve construction exceeds 4 MB");
    return text;
}

Recipe Decode(const std::string& raw) {
    if (raw.size() > kMaxRecipeBytes) throw BadParam("Stored construction exceeds 4 MB");
    const json data = json::parse(raw, nullptr, false);
    if (data.is_discarded()) throw BadParam("the stored construction is not JSON");
    Keys(data, "schema version definition origin kind alignment history topology", "stored construction");
    if (Field(data, "schema") != json(kSchema) || Field(data, "version") != json(1))
        throw BadParam("Unsupported curve construction version");
    Recipe r;
    const json origin = Field(data, "origin");
    if (origin.type() != json::value_t::array || origin.size() != 3) throw BadParam("origin must contain 3 finite numbers");
    r.origin = {Num(origin[0], "origin"), Num(origin[1], "origin"), Num(origin[2], "origin")};
    const json kind = Field(data, "kind");
    if (kind != json("curve") && kind != json("sweep") && kind != json("loft")) throw BadParam("Invalid construction kind");
    r.kind = kind.get<std::string>();
    const json topology = Field(data, "topology");
    if (topology.type() != json::value_t::string || topology.get<std::string>().size() != 64)
        throw BadParam("Invalid topology signature");
    r.topology = topology.get<std::string>();
    const json history = Field(data, "history");
    if (history.type() != json::value_t::array || history.empty() || history.size() > kHistoryLimit)
        throw BadParam("Invalid construction history");
    for (const json& row : history) {
        Keys(row, "parameters fingerprint", "construction state");
        const json fingerprint = Field(row, "fingerprint");
        if (fingerprint.type() != json::value_t::string || !IsFingerprint(fingerprint.get<std::string>()))
            throw BadParam("Invalid construction fingerprint");
        Revision revision{ParseValues(Field(row, "parameters")), fingerprint.get<std::string>()};
        const auto sameNames = [](const Values& a, const Values& b) {
            return std::equal(a.begin(), a.end(), b.begin(), b.end(), [](const auto& x, const auto& y) { return x.first == y.first; });
        };
        if (!r.history.empty() && !sameNames(r.history.back().parameters, revision.parameters))
            throw BadParam("Parameter names changed across history");
        r.history.push_back(revision);
    }
    r.definition = Field(data, "definition");
    if (r.definition.type() != json::value_t::object) throw BadParam("Missing construction recipe");
    r.alignment = Field(data, "alignment");
    return r;
}

// The recipe lives in MAXScript AppData, as the tool keeps it.
std::optional<std::string> ReadRecipe(INode* node) { return GetAppData(node, kRecipeSlot); }
void WriteRecipe(INode* node, const std::string& raw) { SetAppData(node, kRecipeSlot, raw, "curve_model recipe"); }

std::optional<Values> Active(const Recipe& r, const std::optional<std::string>& fingerprint) {
    std::vector<const Values*> matches;
    for (const Revision& row : r.history)
        if (fingerprint && row.fingerprint == *fingerprint) matches.push_back(&row.parameters);
    if (matches.empty()) return std::nullopt;
    for (const Values* m : matches)
        if (*m != *matches.front()) return std::nullopt;
    return *matches.back();
}

std::string ModelToken(INode* node, const std::optional<std::string>& fingerprint, const std::string& raw) {
    return HexDigest(Sha256(std::to_string(Animatable::GetHandleByAnim(node)) + "|" + fingerprint.value_or("") + "|" + raw));
}

struct Stored {
    std::string raw;
    Recipe recipe;
};

Stored LoadRecipe(INode* node) {
    const std::optional<std::string> raw = ReadRecipe(node);
    if (!raw) throw NotFound(Quoted(node) + " has no curve_model recipe; build one with curve_create()");
    try {
        return {*raw, Decode(*raw)};
    } catch (const MaxError& error) {
        throw MaxError(Quoted(node) + " has an unreadable curve_model recipe: " + error.what());
    }
}

nb::dict ModelReport(INode* node, const Stored& s, bool includeDefinition) {
    const std::optional<std::string> fingerprint = Fingerprint(node);
    const std::optional<Values> active = Active(s.recipe, fingerprint);
    nb::dict out;
    out["kind"] = s.recipe.kind;
    out["parameters"] = active ? nb::object(ValuesDict(*active)) : nb::none();
    out["model_token"] = ModelToken(node, fingerprint, s.raw);
    out["geometry_matches_recipe"] = active.has_value();
    if (!active)
        out["reason"] = "Manual edit, ambiguous state or geometry outside retained undo history; parameter updates blocked";
    out["retained_parameter_states"] = s.recipe.history.size();
    nb::list names;
    const json curves = Field(s.recipe.definition, "curves");
    if (curves.type() == json::value_t::object)
        for (auto it = curves.begin(); it != curves.end(); ++it) names.append(it.key());
    out["curve_names"] = names;
    out["origin"] = Tuple(s.recipe.origin);
    out["modifiers_above"] = NodeModifiers(Wrap(node)).size();
    out["instance_count"] = InstanceCount(node);
    if (includeDefinition) out["definition"] = ToPython(s.recipe.definition);
    return out;
}

Node CurveCreate(CurveDefinitionArg definition, const std::string& name, ParametersArg parameters) {
    RequireMain();
    const json def = FromPython(definition.value);
    const Values values = ParseValues(FromPython(parameters.value));
    if (def.dump().size() > kMaxRecipeBytes) throw BadParam("Curve construction exceeds 4 MB");
    const Model m = BuildModel(def, values, json());
    std::vector<V3> points = m.vertices;
    if (m.kind == "curve")
        for (const CurveKnot& k : KnotsOf(m.curve)) points.push_back(k.pos);
    const V3 origin = Center(points);
    INode* node = m.kind == "curve" ? CreateCurveNode(name, m.curve, origin) : CreateMeshNode(name, m, origin);
    try {
        const Recipe r{def, origin, m.kind, m.alignment, HexDigest(Sha256(TopologyText(m))), {{values, *Fingerprint(node)}}};
        WriteRecipe(node, Encode(r));
    } catch (...) {
        GetCOREInterface()->DeleteNode(node, FALSE);  // a failed create leaves nothing behind
        throw;
    }
    return Wrap(node);
}

// Writes the model's geometry over the base in place and returns how to put
// the previous geometry back.
std::function<void()> Rewrite(INode* node, const Model& m, const V3& origin) {
    Object* base = BaseObject(node);
    if (m.kind == "curve") {
        auto* ss = static_cast<SplineShape*>(base);
        Spline3D* sp = ss->shape.GetSpline(0);
        const std::vector<CurveKnot> knots = KnotsOf(m.curve);
        if (sp->KnotCount() != int(knots.size())) throw MaxError(Quoted(node) + "'s spline no longer matches its recipe");
        auto backup = std::make_shared<Spline3D>(*sp);
        for (int i = 0; i < int(knots.size()); ++i) {
            sp->SetKnotPoint(i, ToP3(knots[i].pos - origin));
            sp->SetInVec(i, ToP3(knots[i].in - origin));
            sp->SetOutVec(i, ToP3(knots[i].out - origin));
        }
        Commit(ss);
        return [ss, sp, backup] { *sp = *backup; Commit(ss); };
    }
    EPoly* poly = nullptr;
    MNMesh* mesh = PolyMesh(base, &poly);
    if (mesh->VNum() != int(m.vertices.size())) throw MaxError(Quoted(node) + "'s cage no longer matches its recipe");
    std::vector<Point3> backup;
    for (int i = 0; i < mesh->VNum(); ++i) {
        backup.push_back(mesh->V(i)->p);
        mesh->V(i)->p = ToP3(m.vertices[i] - origin);
    }
    poly->LocalDataChanged(GEOM_CHANNEL);
    return [mesh, poly, backup] {
        for (int i = 0; i < int(backup.size()); ++i) mesh->V(i)->p = backup[i];
        poly->LocalDataChanged(GEOM_CHANNEL);
    };
}

nb::dict CurveUpdate(const Node& n, ParametersArg parameters, const std::optional<std::string>& expected) {
    INode* node = Resolve(n);
    const Values values = ParseValues(FromPython(parameters.value));
    if (values.empty()) throw BadParam("curve_update needs parameters to change: node.curve_update({'width': 5})");
    Stored s = LoadRecipe(node);
    const std::optional<std::string> fingerprint = Fingerprint(node);
    if (expected && *expected != ModelToken(node, fingerprint, s.raw))
        throw MaxError("STALE_MODEL: the construction of " + Quoted(node) + " changed; read node.curve_model() again");
    const std::optional<Values> current = Active(s.recipe, fingerprint);
    if (!current)
        throw MaxError(Quoted(node) + " was edited by hand or is outside its retained undo history; "
                       "its construction parameters cannot be updated");
    if (InstanceCount(node) > 1)
        throw MaxError(Quoted(node) + " shares its base with other nodes (instances); make it unique before changing parameters");
    std::vector<std::string> unknown, known;
    for (const auto& [key, value] : *current) known.push_back(key);
    for (const auto& [key, value] : values)
        if (!current->count(key)) unknown.push_back(key);
    if (!unknown.empty())
        throw BadParam("unknown construction parameters: " + Join(unknown) + "." + Suggest(unknown.front(), known) +
                       " Parameters: " + Join(known));
    Values updated = *current;
    for (const auto& [key, value] : values) updated[key] = value;
    if (updated == *current) {
        nb::dict out = ModelReport(node, s, false);
        out["unchanged"] = true;
        return out;
    }
    const Model m = BuildModel(s.recipe.definition, updated, s.recipe.alignment);
    if (HexDigest(Sha256(TopologyText(m))) != s.recipe.topology)
        throw BadParam("this parameter change alters the topology; build a separate construction with curve_create()");
    const std::string oldRaw = s.raw;
    const std::function<void()> restore = Rewrite(node, m, s.recipe.origin);
    try {
        const std::string state = *Fingerprint(node);
        for (const Revision& row : s.recipe.history)
            if (row.fingerprint == state && row.parameters != updated)
                throw MaxError("Different parameters produce an existing geometry state; undo would be ambiguous");
        s.recipe.history.push_back({updated, state});
        if (s.recipe.history.size() > kHistoryLimit) s.recipe.history.erase(s.recipe.history.begin());
        s.raw = Encode(s.recipe);
        WriteRecipe(node, s.raw);
    } catch (...) {
        restore();
        if (ReadRecipe(node) != oldRaw) WriteRecipe(node, oldRaw);
        throw;
    }
    nb::dict out = ModelReport(node, s, false);
    out["preview"] = Preview(m);
    nb::dict changed;
    for (const auto& [key, value] : values) {
        nb::dict row;
        row["before"] = current->at(key);
        row["after"] = updated.at(key);
        changed[key.c_str()] = row;
    }
    out["changed_parameters"] = changed;
    return out;
}

void ConvertToSpline(const Node& n) {
    INode* node = Resolve(n);
    const ObjectState os = node->EvalWorldState(Now());
    if (!os.obj || os.obj->SuperClassID() != SHAPE_CLASS_ID)
        throw BadParam(Quoted(node) + " is " + WithArticle(ClassOf(node)) + ", not a shape; only shapes become editable splines");
    {
        ScopedMaxScriptEvaluationContext context;
        one_value_local(target);
        vl.target = MAXNode::intern(node);
        Value* args[] = {vl.target};
        MxsCall(L"convertToSplineShape", args, 1);
    }
    if (!IsEditableSpline(BaseObject(node))) throw MaxError("3ds Max did not convert " + Quoted(node) + " to an editable spline");
}

// ── Python values ────────────────────────────────────────────────

std::string PointText(const Point3& p) {
    char text[96];
    std::snprintf(text, sizeof text, "(%g, %g, %g)", p.x, p.y, p.z);
    return text;
}

std::string KnotRepr(const Knot& k) {
    std::string out = "<Knot " + (k.type ? *k.type + " " : std::string()) + PointText(k.pos);
    const bool handles = !k.type || *k.type == "bezier" || *k.type == "bezier_corner";
    if (handles && k.in) out += " in " + PointText(*k.in);
    if (handles && k.out) out += " out " + PointText(*k.out);
    if (k.seg) out += " " + *k.seg;
    return out + ">";
}

std::string SplineRepr(const SplineRef& r) {
    const std::string head = "<Spline " + std::to_string(r.index) + " of ";
    INode* node = HandlerHelpers::FindNodeByHandle(r.node);
    if (!InScene(node)) return head + "#" + std::to_string(r.node) + " deleted>";
    Object* base = BaseObject(node);
    if (!IsEditableSpline(base)) return head + Quoted(node) + ", not an editable spline>";
    BezierShape& shape = static_cast<SplineShape*>(base)->shape;
    if (r.index >= shape.SplineCount()) return head + Quoted(node) + " removed>";
    Spline3D* sp = shape.GetSpline(r.index);
    return head + Quoted(node) + ": " + std::to_string(sp->KnotCount()) + " knots, " + (sp->Closed() ? "closed" : "open") + ">";
}

} // namespace

void BindCurves(nb::module_& m) {
    nb::class_<Knot>(m, "Knot",
        "A spline knot value: Knot(pos, type, in_vec, out_vec, seg), world space. Handles are absolute positions; "
        "seg is the segment leaving the knot.")
        .def("__init__", [](Knot* k, nb::handle pos, const std::optional<std::string>& type, nb::handle in,
                            nb::handle out, const std::optional<std::string>& seg) {
            new (k) Knot{ToPoint3(pos, "pos"), OptionalKnotType(type), OptionalPoint(in, "in_vec"),
                         OptionalPoint(out, "out_vec"), OptionalSegment(seg)};
        }, "pos"_a, "type"_a = nb::none(), "in_vec"_a = nb::none(), "out_vec"_a = nb::none(), "seg"_a = nb::none())
        .def_prop_rw("pos", [](const Knot& k) { return AsPoint{k.pos}; },
            [](Knot& k, PointArg v) { k.pos = ToPoint3(v.value, "pos"); }, "World position (x, y, z).")
        .def_prop_rw("type", [](const Knot& k) { return k.type; },
            [](Knot& k, const std::optional<std::string>& t) { k.type = OptionalKnotType(t); },
            "corner, smooth, bezier or bezier_corner (bounded when creating); None takes the call's default.")
        .def_prop_rw("in_vec",
            [](const Knot& k) { return k.in ? std::optional<AsPoint>(AsPoint{*k.in}) : std::nullopt; },
            [](Knot& k, std::optional<PointArg> v) { k.in = v ? OptionalPoint(v->value, "in_vec") : std::nullopt; },
            nb::for_setter(nb::arg("in_vec").none()),
            "Incoming handle position; kept by bezier and bezier_corner knots, computed for the others.")
        .def_prop_rw("out_vec",
            [](const Knot& k) { return k.out ? std::optional<AsPoint>(AsPoint{*k.out}) : std::nullopt; },
            [](Knot& k, std::optional<PointArg> v) { k.out = v ? OptionalPoint(v->value, "out_vec") : std::nullopt; },
            nb::for_setter(nb::arg("out_vec").none()),
            "Outgoing handle position; kept by bezier and bezier_corner knots, computed for the others.")
        .def_prop_rw("seg", [](const Knot& k) { return k.seg; },
            [](Knot& k, const std::optional<std::string>& s) { k.seg = OptionalSegment(s); },
            "line or curve: the segment leaving this knot; None on the last knot of an open spline.")
        .def("__eq__", [](const Knot& a, nb::handle b) {
            if (!nb::isinstance<Knot>(b)) return false;
            const Knot o = nb::cast<Knot>(b);
            return a.pos == o.pos && a.type == o.type && a.in == o.in && a.out == o.out && a.seg == o.seg;
        }, nb::arg("other").none())
        .def("__repr__", &KnotRepr);

    nb::class_<Segment>(m, "Segment",
        "A spline segment snapshot: type (line or curve), world length, and its cubic's control points.")
        .def_ro("type", &Segment::type, "line or curve.")
        .def_ro("length", &Segment::length, "World length of the segment.")
        .def_prop_ro("points", [](const Segment& s) {
            return nb::typed<nb::tuple, AsPoint, AsPoint, AsPoint, AsPoint>(
                nb::make_tuple(Tuple(s.points[0]), Tuple(s.points[1]), Tuple(s.points[2]), Tuple(s.points[3])));
        }, "(start, start handle, end handle, end) in world space; a line's handles lie on its chord.")
        .def("__repr__", [](const Segment& s) {
            char length[32];
            std::snprintf(length, sizeof length, "%g", s.length);
            return "<Segment " + s.type + " " + length + ">";
        });

    nb::class_<SplineRef>(m, "Spline",
        "One spline of an editable shape (node.splines[i]). World space, 0-based indices; every edit takes "
        "expected=node.curve_token to refuse a shape that changed.")
        .def_prop_ro("index", [](const SplineRef& r) { return r.index; },
            "The spline's position in node.splines, from 0.")
        .def_prop_ro("node", [](const SplineRef& r) { Resolve(Node{r.node}); return Node{r.node}; },
            "The node whose shape holds this spline.")
        .def_prop_ro("closed", [](const SplineRef& r) { return ViewOf(r).spline->Closed() != 0; },
            "Whether the spline is closed; change it with close() or open().")
        .def_prop_ro("knots", &ReadKnots, "The knots as Knot values; edit them with set_knot or set_knots.")
        .def_prop_ro("segments", &Segments, "The segments as Segment snapshots, segment i leaving knot i.")
        .def_prop_ro("length", &SplineLength, "World length of the spline.")
        .def("set_knot", &SetKnot, "index"_a, nb::kw_only(), "pos"_a.none() = nb::none(), "in_vec"_a.none() = nb::none(),
            "out_vec"_a.none() = nb::none(), "type"_a = nb::none(), "seg"_a = nb::none(), "expected"_a = nb::none(),
            "Change one knot; a moved knot carries its handles: set_knot(2, pos=(10, 0, 0)), "
            "set_knot(2, type='bezier_corner', out_vec=(14, 3, 0)).")
        .def("set_knots", &SetKnots, "knots"_a, nb::kw_only(), "expected"_a = nb::none(),
            "Rewrite every knot at once from Knots, dicts or points (one per knot); checked before anything is "
            "written. A dict has 'pos' and any of 'type', 'in_vec', 'out_vec' and 'seg'; what an entry leaves out "
            "or gives as None is kept, and a moved knot carries its handles. ks = s.knots; ks[1].pos = (5, 2, 0); "
            "s.set_knots(ks).")
        .def("insert", &Insert, "segment"_a, "param"_a = 0.5, nb::kw_only(), "expected"_a = nb::none(),
            "Split a segment at param (0-1, exclusive) with a new knot; returns its index.")
        .def("delete_knot", &DeleteKnot, "index"_a, nb::kw_only(), "expected"_a = nb::none(), "Delete a knot.")
        .def("reverse", [](const SplineRef& r, const std::optional<std::string>& expected) {
            EditSpline(r, expected, NoCheck, [](BezierShape&, Spline3D* sp) { sp->Reverse(TRUE); });
        }, nb::kw_only(), "expected"_a = nb::none(), "Reverse the knot order, keeping the first knot first.")
        .def("close", [](const SplineRef& r, const std::optional<std::string>& expected) {
            EditSpline(r, expected, [](Spline3D* sp) {
                if (sp->KnotCount() < 3) throw BadParam("closing needs at least three knots");
            }, [](BezierShape&, Spline3D* sp) { sp->SetClosed(); });
        }, nb::kw_only(), "expected"_a = nb::none(), "Close the spline with a segment from the last knot to the first.")
        .def("open", [](const SplineRef& r, const std::optional<std::string>& expected) {
            EditSpline(r, expected, NoCheck, [](BezierShape&, Spline3D* sp) { sp->SetOpen(); });
        }, nb::kw_only(), "expected"_a = nb::none(), "Open the spline between its last and first knots.")
        .def("delete", [](const SplineRef& r, const std::optional<std::string>& expected) {
            EditSpline(r, expected, NoCheck, [&](BezierShape& shape, Spline3D*) { shape.DeleteSpline(r.index); });
        }, nb::kw_only(), "expected"_a = nb::none(), "Delete the spline from its shape; later splines move down an index.")
        .def("sample", [](const SplineRef& r, int count) -> nb::typed<nb::list, AsPoint> {
            nb::list out;
            for (const nb::object& point : Sample(r, count)) out.append(point);
            return out;
        }, "count"_a,
            "count points evenly spaced by length along the spline, world space (closed: no repeated end).")
        .def("qa", &SplineQa, "tolerance"_a = 0.01,
            "Sampled QA report: length, planarity, self-intersections, tangent breaks and segments that backtrack. "
            "complete is False, with a reason, for a spline it could not sample. "
            "Keys: {'complete', 'reason'?, 'closed'?, 'length'?, 'endpoint_gap'?, 'planar'?, 'planarity_deviation'?, "
            "'sampled_intersections'?, 'intersection_samples'?, 'intersection_check'?, 'tangent_breaks'?, "
            "'segment_backtracking'?, 'backtracking_check'?, 'tolerance'?, 'sampled_spans'?, 'bounds'?}.")
        .def("capture", &Capture, "knots"_a = nb::none(),
            "Capture the AGENT VIEWPORT with knots labeled K<i> and handles I<i>/O<i>; returns {'file', 'view', "
            "'width', 'height', 'labels', 'labels_truncated'}; pass 'view' to pick(). "
            "Keys: {'file', 'view', 'width', 'height', 'labels', 'labels_truncated'}.")
        .def("pick", &Pick, "x"_a, "y"_a, "view"_a, "tolerance"_a = 0.025, "knots"_a = nb::none(),
            "Knots and handles near image point (x, y) of a capture (fractions 0-1), nearest first; view is the "
            "capture's view_token. Overlapping front and back handles stay ambiguous. Each is {'knot': index, "
            "'component': 'knot', 'in_handle' or 'out_handle', 'point': world position, 'type': the knot's type, "
            "'distance': pixels from (x, y), 'image': its (x, y) fractions}.")
        .def("__len__", [](const SplineRef& r) { return ViewOf(r).spline->KnotCount(); })
        .def("__repr__", &SplineRepr)
        .def("__eq__", [](const SplineRef& a, nb::handle b) {
            if (!nb::isinstance<SplineRef>(b)) return false;
            const SplineRef o = nb::cast<SplineRef>(b);
            return a.node == o.node && a.index == o.index;
        }, nb::arg("other").none())
        .def("__hash__", [](const SplineRef& r) { return Py_hash_t(r.node * 1000003ULL ^ unsigned(r.index)); });

    AddProperty<Node>("splines", [](const Node& n) {
        SplineShape* ss = EditableSpline(Resolve(n));
        std::vector<SplineRef> out;
        for (int i = 0; i < ss->shape.SplineCount(); ++i) out.push_back({n.handle, i});
        return out;
    }, "The splines of an editable spline base (SplineShape or Line).");
    AddMethod<Node>("add_spline", &AddSpline, "points"_a, "closed"_a = false, "knot"_a = "smooth", nb::kw_only(),
        "expected"_a = nb::none(),
        "Add a spline from world points, dicts or Knots, as spline() takes them, to an editable shape (a hole, "
        "another outline); returns it.");
    AddProperty<Node>("curve_token", [](const Node& n) {
        INode* node = Resolve(n);
        return CurveToken(node, EditableSpline(node));
    }, "Token of the base knots, topology and object transform; pass it as expected= so edits refuse a changed shape.");
    AddMethod<Node>("convert_to_spline", &ConvertToSpline,
        "Collapse a shape to an editable spline (convertToSplineShape), discarding its modifier stack.");
    AddMethod<Node>("curve_model", [](const Node& n, bool includeDefinition) {
        INode* node = Resolve(n);
        return ModelReport(node, LoadRecipe(node), includeDefinition);
    }, "include_definition"_a = false,
        "The node's curve_model recipe: kind, parameters, model_token, whether the geometry still matches it (reason "
        "says why not), and its definition with include_definition=True. "
        "Keys: {'kind', 'parameters', 'model_token', 'geometry_matches_recipe', 'reason'?, 'retained_parameter_states', "
        "'curve_names', 'origin', 'modifiers_above', 'instance_count', 'definition'?}.");
    AddMethod<Node>("curve_update", &CurveUpdate, "parameters"_a, nb::kw_only(), "expected"_a = nb::none(),
        "Change curve_model parameters in place, keeping placement, materials and modifiers: "
        "node.curve_update({'bow': 6}, expected=node.curve_model()['model_token']). Returns the curve_model report "
        "with unchanged when the values already were these, else preview and changed_parameters {name: {'before', 'after'}}. "
        "Keys: {'kind', 'parameters', 'model_token', 'geometry_matches_recipe', 'retained_parameter_states', "
        "'curve_names', 'origin', 'modifiers_above', 'instance_count', 'unchanged'?, 'preview'?, 'changed_parameters'?}.");

    m.def("spline", &CreateSpline, "points"_a, "name"_a = "", "closed"_a = false, "knot"_a = "smooth",
          "center_pivot"_a = false,
          "Create an editable spline from world points, dicts or Knots: spline([(0, 0, 0), (40, 0, 0), (40, 30, 0)], "
          "'Rail', knot='corner'). knot: corner, smooth, bezier, bezier_corner or bounded (short handles that do not "
          "overshoot). At least two entries; a dict is {'pos', 'type', 'in_vec', 'out_vec', 'seg'} with only 'pos' "
          "required, and a bezier or bezier_corner knot needs both handles.");
    m.def("curve_preview", [](CurveDefinitionArg definition, ParametersArg parameters) {
        RequireMain();
        return Preview(BuildModel(FromPython(definition.value), ParseValues(FromPython(parameters.value)), json()));
    }, "definition"_a, "parameters"_a.none() = nb::none(),
          "Evaluate a curve_model definition without touching the scene: counts, bounds and per-curve QA. "
          "definition={'curves': {name: curve}, 'output': {'kind': 'curve' | 'sweep' | 'loft', ...}, 'tolerance': "
          "0.01}; parameters {'width': 4} are the numbers its expressions name. mx.help('curve_model') has the "
          "grammar of curves and outputs. "
          "Keys: {'kind', 'curves', 'alignment', 'bounds'?, 'notes', 'counts'}.");
    m.def("curve_create", &CurveCreate, "definition"_a, "name"_a = "", "parameters"_a.none() = nb::none(),
          "Build a curve_model definition as an editable spline or quad cage centered on its bounds, saving the "
          "recipe for node.curve_update. definition and parameters are curve_preview's: {'curves': {name: curve}, "
          "'output': {'kind': 'curve' | 'sweep' | 'loft', ...}} and {'width': 4}; mx.help('curve_model') has the "
          "grammar.");
}

} // namespace MX_ABI_TAG
} // namespace mx
