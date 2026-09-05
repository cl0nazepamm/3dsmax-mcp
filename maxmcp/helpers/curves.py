"""Small, deterministic curve construction language. No Max or optional dependencies.

Lengths use scene units, angles use degrees, expressions use the bounded loft
evaluator. Curves are cubic Beziers (circular arcs are cubic approximations).
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import math
from typing import Any

from .loft import coordinate, validate_parameters, build_loft
from .mesh import integer

EPS = 1e-8
MAX_SEGMENTS = 256
MAX_SAMPLES = 1000


def add(a, b): return [x + y for x, y in zip(a, b)]
def sub(a, b): return [x - y for x, y in zip(a, b)]
def mul(a, k): return [x * k for x in a]
def dot(a, b): return sum(x * y for x, y in zip(a, b))
def cross(a, b): return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
def length(a): return math.sqrt(dot(a, a))
def unit(a):
    n = length(a)
    if n <= EPS:
        raise ValueError("A direction or segment has zero length")
    return mul(a, 1/n)
def lerp(a, b, t): return add(mul(a, 1-t), mul(b, t))


def keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    extra = set(value) - set(allowed.split())
    if extra:
        raise ValueError(f"Unknown {label} fields: {sorted(extra)}")


def boolean(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")
    return value


def vec(value, params):
    if not isinstance(value, (list, tuple)) or len(value) not in (2, 3):
        raise ValueError("Curve points/directions need two or three coordinates")
    return [coordinate(v, params) for v in value] + ([0.] if len(value) == 2 else [])


def frame(spec, params):
    presets = {"xy": ([1,0,0], [0,0,1]), "xz": ([1,0,0], [0,-1,0]), "yz": ([0,1,0], [1,0,0])}
    if spec is None: spec = "xy"
    if isinstance(spec, str):
        if spec.lower() not in presets:
            raise ValueError("plane must be xy, xz, yz, or {origin,x_axis,normal}")
        x, z = presets[spec.lower()]
        return [0.,0.,0.], x, cross(z,x), z
    keys(spec, "origin x_axis normal", "plane")
    origin = vec(spec.get("origin", [0,0,0]), params)
    x = unit(vec(spec.get("x_axis", [1,0,0]), params))
    z = unit(vec(spec.get("normal", [0,0,1]), params))
    if abs(dot(x,z)) > 1e-6:
        raise ValueError("plane.x_axis must be perpendicular to plane.normal")
    return origin, x, unit(cross(z,x)), z


@dataclass
class Segment:
    start: list
    out: list
    incoming: list
    end: list
    label: str = ""
    straight: bool = False

    def at(self, t):
        u = 1-t
        return [u*u*u*a + 3*u*u*t*b + 3*u*t*t*c + t*t*t*d
                for a,b,c,d in zip(self.start,self.out,self.incoming,self.end)]


def line(a, b, label=""):
    if length(sub(a,b)) <= EPS:
        raise ValueError("Zero-length line; remove coincident points")
    return Segment(a, lerp(a,b,1/3), lerp(a,b,2/3), b, label, True)


def arc(center, radius, start, sweep, label=""):
    if radius <= EPS or not EPS < abs(sweep) <= 360:
        raise ValueError("Arc requires positive radius and nonzero sweep within +/-360 degrees")
    count = math.ceil(abs(sweep)/90)
    result = []
    for i in range(count):
        a, b = math.radians(start+sweep*i/count), math.radians(start+sweep*(i+1)/count)
        h = 4/3 * math.tan((b-a)/4) * radius
        p = add(center, [radius*math.cos(a),radius*math.sin(a),0])
        q = add(center, [radius*math.cos(b),radius*math.sin(b),0])
        result.append(Segment(p, add(p,[-h*math.sin(a),h*math.cos(a),0]),
                              sub(q,[-h*math.sin(b),h*math.cos(b),0]),q,label))
    return result


def _arc_between(a, b, radius, clockwise, label):
    if abs(a[2]-b[2]) > EPS:
        raise ValueError("Radius arcs must lie in the sketch XY plane")
    delta = sub(b,a)
    chord = length(delta)
    if radius <= EPS or chord <= EPS or chord > 2*radius + EPS:
        raise ValueError("Arc radius is too small for its chord")
    normal = [-delta[1]/chord, delta[0]/chord, 0]
    sign = -1 if clockwise else 1
    center = add(lerp(a,b,.5), mul(normal,sign*math.sqrt(max(0,radius*radius-chord*chord/4))))
    start = math.degrees(math.atan2(a[1]-center[1],a[0]-center[0]))
    sweep = sign*math.degrees(2*math.asin(min(1,chord/(2*radius))))
    return arc(center,radius,start,sweep,label)


def _tangent_arc(a, b, tangent, label):
    t = unit(tangent)
    if abs(t[2]) > EPS or abs(a[2]-b[2]) > EPS:
        raise ValueError("Tangent arcs must lie in the sketch XY plane")
    d = sub(b,a)
    n = [-t[1],t[0],0]
    denominator = 2*dot(d,n)
    if abs(denominator) <= EPS:
        raise ValueError("Tangent arc is straight/undefined; use a line")
    signed_radius = dot(d,d)/denominator
    c = add(a,mul(n,signed_radius))
    start = math.degrees(math.atan2(a[1]-c[1],a[0]-c[0]))
    end = math.degrees(math.atan2(b[1]-c[1],b[0]-c[0]))
    sweep = (end-start)%360 if signed_radius > 0 else -((start-end)%360)
    return arc(c,abs(signed_radius),start,sweep,label)


def _fillet(points, radius, closed):
    if radius <= EPS:
        raise ValueError("fillet must be positive; omit it for sharp corners")
    corners = []
    for i,p in enumerate(points):
        if not closed and i in (0,len(points)-1):
            corners.append((p,p,[])); continue
        before, after = points[i-1], points[(i+1)%len(points)]
        u,v = unit(sub(before,p)),unit(sub(after,p))
        theta = math.acos(max(-1,min(1,dot(u,v))))
        if theta <= EPS or math.pi-theta <= EPS:
            raise ValueError("Fillet needs a noncollinear corner")
        distance = radius/math.tan(theta/2)
        a,b = add(p,mul(u,distance)),add(p,mul(v,distance))
        c = add(p,mul(unit(add(u,v)),radius/math.sin(theta/2)))
        start = math.degrees(math.atan2(a[1]-c[1],a[0]-c[0]))
        sign = 1 if cross(mul(u,-1),v)[2] > 0 else -1
        pieces = arc(c,radius,start,sign*(180-math.degrees(theta)),f"corner_{i+1}")
        corners.append((a,b,pieces))
    result = []
    for i in range(len(points) if closed else len(points)-1):
        j=(i+1)%len(points)
        if dot(sub(corners[j][0],corners[i][1]),sub(points[j],points[i])) <= EPS:
            raise ValueError("Fillets overlap; reduce radius or lengthen the adjacent edges")
        result.append(line(corners[i][1],corners[j][0],f"edge_{i+1}"))
        result.extend(corners[j][2])
    return result


def _offset_polygon(points, amount):
    area = sum(cross(a,b)[2] for a,b in zip(points,points[1:]+points[:1]))/2
    if abs(area) <= EPS:
        raise ValueError("Offset needs a simple closed planar polygon")
    sign = 1 if area > 0 else -1
    out = []
    for i,p in enumerate(points):
        u,v=unit(sub(p,points[i-1])),unit(sub(points[(i+1)%len(points)],p))
        n1,n2=[sign*u[1],-sign*u[0],0],[sign*v[1],-sign*v[0],0]
        denominator=1+dot(n1,n2)
        if denominator < 1e-6:
            raise ValueError("Offset has an unbounded miter at a reversing edge")
        out.append(add(p,mul(add(n1,n2),amount/denominator)))
    # An inward offset must not pass through an edge and reappear inverted.
    for i in range(len(points)):
        if dot(sub(out[(i+1)%len(out)],out[i]),sub(points[(i+1)%len(points)],points[i])) <= EPS:
            raise ValueError("Offset collapsed or inverted an edge")
    return out


def make_curve(spec, params):
    keys(spec,"kind plane points closed radius width depth start_angle sweep center start segments tension fillet offset", "curve")
    kind=spec.get("kind")
    fields={"polyline":"points fillet offset", "spline":"points tension", "bezier":"points",
            "path":"start segments", "circle":"radius center start_angle",
            "arc":"radius center start_angle sweep", "rounded_rectangle":"width depth radius"}
    if not isinstance(kind,str) or kind not in fields:
        raise ValueError("Curve kind must be polyline, spline, bezier, path, arc, circle or rounded_rectangle")
    keys(spec,"kind plane closed "+fields[kind],f"{kind} curve")
    origin,x,y,z=frame(spec.get("plane"),params)
    def world(p): return add(origin,add(mul(x,p[0]),add(mul(y,p[1]),mul(z,p[2]))))
    closed=boolean(spec.get("closed",kind in {"circle","rounded_rectangle"}),"closed")
    pieces=[]
    if kind in {"circle","arc"}:
        if kind=="circle" and not closed: raise ValueError("circle is closed")
        pieces=arc(vec(spec.get("center",[0,0]),params),coordinate(spec.get("radius"),params),
                   coordinate(spec.get("start_angle",0),params),360 if kind=="circle" else coordinate(spec.get("sweep",90),params),"arc")
        if kind=="arc" and closed and length(sub(pieces[0].start,pieces[-1].end))>EPS:
            raise ValueError("Use a path with a closing line for a closed partial arc")
    elif kind=="rounded_rectangle":
        if not closed: raise ValueError("rounded_rectangle is closed")
        w,d,r=(coordinate(spec.get(k),params) for k in ("width","depth","radius"))
        if w<=EPS or d<=EPS or not EPS<r<min(w,d)/2:
            raise ValueError("Rounded rectangle needs 0 < radius < half its smaller dimension")
        points=[[-w/2,-d/2,0],[w/2,-d/2,0],[w/2,d/2,0],[-w/2,d/2,0]]
        pieces=_fillet(points,r,True)
    elif kind in {"polyline","spline","bezier"}:
        rows=spec.get("points")
        if not isinstance(rows,list) or not 2<=len(rows)<=MAX_SEGMENTS:
            raise ValueError("points needs 2-256 points")
        points=[vec(p,params) for p in rows]
        if closed and len(points)<3: raise ValueError("Closed curves need at least three points")
        if any(length(sub(points[i],points[i-1]))<=EPS for i in range(1,len(points))):
            raise ValueError("Consecutive curve points coincide")
        if closed and length(sub(points[0],points[-1]))<=EPS:
            raise ValueError("Closed curves wrap automatically; omit the duplicated endpoint")
        if kind=="bezier":
            if len(points)!=4 or closed: raise ValueError("bezier needs four control points and closed=false")
            pieces=[Segment(*points,"bezier")]
        elif kind=="polyline":
            if "offset" in spec:
                if not closed or any(abs(p[2])>EPS for p in points):
                    raise ValueError("offset requires a closed polygon on its local XY plane")
                points=_offset_polygon(points,coordinate(spec["offset"],params))
            if "fillet" in spec:
                if any(abs(p[2])>EPS for p in points): raise ValueError("fillet requires planar XY points")
                pieces=_fillet(points,coordinate(spec["fillet"],params),closed)
            else:
                pieces=[line(points[i],points[(i+1)%len(points)],f"edge_{i+1}") for i in range(len(points) if closed else len(points)-1)]
        else:
            tension=coordinate(spec.get("tension",0),params)
            if not 0<=tension<1: raise ValueError("spline tension must be in [0,1)")
            tangents=[]
            for i,p in enumerate(points):
                before=points[i-1] if i or closed else p
                after=points[(i+1)%len(points)] if closed or i<len(points)-1 else p
                tangents.append(mul(sub(after,before),(1-tension)*(.5 if before!=p and after!=p else 1)))
            for i in range(len(points) if closed else len(points)-1):
                j=(i+1)%len(points)
                pieces.append(Segment(points[i],add(points[i],mul(tangents[i],1/3)),sub(points[j],mul(tangents[j],1/3)),points[j],f"span_{i+1}"))
    elif kind=="path":
        current=vec(spec.get("start"),params)
        rows=spec.get("segments")
        if not isinstance(rows,list) or not 1<=len(rows)<=MAX_SEGMENTS:
            raise ValueError("path.segments needs 1-256 segments")
        for i,row in enumerate(rows):
            keys(row,"kind to radius clockwise tangent start_tangent end_tangent start_length end_length label", "segment")
            end=vec(row.get("to"),params)
            label=row.get("label",f"segment_{i+1}")
            if not isinstance(label,str) or not 1<=len(label)<=64: raise ValueError("segment label needs 1-64 characters")
            segment_kind=row.get("kind","line")
            segment_fields={"line":"", "arc":"radius clockwise", "tangent_arc":"tangent",
                            "bezier":"start_tangent end_tangent start_length end_length"}
            if not isinstance(segment_kind,str) or segment_kind not in segment_fields:
                raise ValueError("Segment kind must be line, arc, tangent_arc or bezier")
            keys(row,"kind to label "+segment_fields[segment_kind],f"{segment_kind} segment")
            if segment_kind=="line": new=[line(current,end,label)]
            elif segment_kind=="arc": new=_arc_between(current,end,coordinate(row.get("radius"),params),boolean(row.get("clockwise",False),"clockwise"),label)
            elif segment_kind=="tangent_arc":
                t=vec(row["tangent"],params) if "tangent" in row else (sub(pieces[-1].end,pieces[-1].incoming) if pieces else None)
                if t is None: raise ValueError("First tangent_arc needs tangent")
                new=_tangent_arc(current,end,t,label)
            elif segment_kind=="bezier":
                t=vec(row["start_tangent"],params) if "start_tangent" in row else (sub(pieces[-1].end,pieces[-1].incoming) if pieces else None)
                if t is None: raise ValueError("First bezier segment needs start_tangent")
                end_t=vec(row.get("end_tangent"),params)
                a=coordinate(row.get("start_length",length(sub(end,current))/3),params)
                b=coordinate(row.get("end_length",length(sub(end,current))/3),params)
                if min(a,b)<=EPS: raise ValueError("Bezier handle lengths must be positive")
                new=[Segment(current,add(current,mul(unit(t),a)),sub(end,mul(unit(end_t),b)),end,label)]
            else: raise ValueError("Segment kind must be line, arc, tangent_arc or bezier")
            pieces.extend(new); current=end
        if closed and length(sub(pieces[0].start,pieces[-1].end))>EPS:
            pieces.append(line(pieces[-1].end,pieces[0].start,"closing_edge"))
    else:
        raise ValueError("Curve kind must be polyline, spline, bezier, path, arc, circle or rounded_rectangle")
    if not 1<=len(pieces)<=MAX_SEGMENTS: raise ValueError("Construction exceeds 256 cubic segments")
    transformed=[Segment(*(world(p) for p in (s.start,s.out,s.incoming,s.end)),s.label,s.straight) for s in pieces]
    return Curve(transformed,closed)


@dataclass
class Curve:
    segments: list[Segment]
    closed: bool

    def knots(self):
        result=[]
        for i,s in enumerate(self.segments):
            prev=self.segments[i-1] if i or self.closed else None
            result.append({"pos":s.start,"in_vec":prev.incoming if prev else s.start,
                           "out_vec":s.out,"type":"bezierCorner","seg":"line" if s.straight else "curve","label":s.label})
        if not self.closed:
            s=self.segments[-1]
            result.append({"pos":s.end,"in_vec":s.incoming,"out_vec":s.end,"type":"bezierCorner","seg":"line","label":"end"})
        return result

    def polyline(self, tolerance=.01):
        points=[self.segments[0].start]
        def flatten(a,b,c,d,depth):
            chord=sub(d,a); norm=length(chord)
            # Control-polygon excess catches collinear backtracking too.
            error=length(sub(a,b))+length(sub(b,c))+length(sub(c,d))-norm
            if norm>EPS: error=max(error,length(cross(sub(b,a),chord))/norm,length(cross(sub(c,a),chord))/norm)
            if error<=tolerance:
                points.append(d); return
            if depth>=16 or len(points)>4096:
                raise ValueError("Curve sampling budget exceeded; increase tolerance or simplify the curve")
            ab,bc,cd=lerp(a,b,.5),lerp(b,c,.5),lerp(c,d,.5)
            abc,bcd=lerp(ab,bc,.5),lerp(bc,cd,.5)
            middle=lerp(abc,bcd,.5)
            flatten(a,ab,abc,middle,depth+1); flatten(middle,bcd,cd,d,depth+1)
        for s in self.segments:
            flatten(s.start,s.out,s.incoming,s.end,0)
        if self.closed: points[-1]=points[0]
        return points

    def samples(self,count,tolerance=.01):
        integer(count,"samples",low=3 if self.closed else 2,high=MAX_SAMPLES)
        points=self.polyline(tolerance)
        distances=[0.]
        for a,b in zip(points,points[1:]): distances.append(distances[-1]+length(sub(a,b)))
        if distances[-1]<=EPS: raise ValueError("Curve has zero length")
        result=[]
        for i in range(count):
            d=distances[-1]*i/(count if self.closed else count-1)
            j=max(1,min(len(points)-1,bisect_left(distances,d)))
            span=distances[j]-distances[j-1]
            result.append(lerp(points[j-1],points[j],(d-distances[j-1])/span if span>EPS else 0))
        return result


def curve_qa(curve,tolerance=.01):
    points=curve.polyline(tolerance)
    if len(points)>1025:
        raise ValueError("Curve QA exceeds 1024 spans; increase tolerance or split the construction")
    base=points[0]
    longest=max((sub(p,base) for p in points),key=length)
    normals=[cross(longest,sub(p,base)) for p in points]
    normal=max(normals,key=length)
    planar=length(normal)<=EPS
    deviation=0.
    if not planar:
        normal=unit(normal)
        deviation=max(abs(dot(sub(p,base),normal)) for p in points)
        planar=deviation<=tolerance
    crossings=[]
    if planar and length(normal)>EPS:
        axes=[i for i in range(3) if i!=max(range(3),key=lambda i:abs(normal[i]))]
        def orient(a,b,c): return (b[axes[0]]-a[axes[0]])*(c[axes[1]]-a[axes[1]])-(b[axes[1]]-a[axes[1]])*(c[axes[0]]-a[axes[0]])
        def touches(a,b,c):
            return abs(orient(a,b,c))<=EPS and all(min(a[k],b[k])-EPS<=c[k]<=max(a[k],b[k])+EPS for k in axes)
        for i,(a,b) in enumerate(zip(points,points[1:])):
            for j in range(i+2,len(points)-1):
                if curve.closed and i==0 and j==len(points)-2: continue
                c,d=points[j],points[j+1]
                if (orient(a,b,c)*orient(a,b,d)<0 and orient(c,d,a)*orient(c,d,b)<0) or any((touches(a,b,c),touches(a,b,d),touches(c,d,a),touches(c,d,b))):
                    crossings.append([i+1,j+1])
    breaks=[]
    segs=curve.segments
    for i in range(len(segs) if curve.closed else len(segs)-1):
        a,b=segs[i],segs[(i+1)%len(segs)]
        u,v=sub(a.end,a.incoming),sub(b.out,b.start)
        if min(length(u),length(v))<=EPS:
            breaks.append({"after_segment":i+1,"angle":None}); continue
        angle=math.degrees(math.acos(max(-1,min(1,dot(unit(u),unit(v))))))
        if angle>.1: breaks.append({"after_segment":i+1,"angle":angle})
    return {"complete":True,"closed":curve.closed,"length":sum(length(sub(a,b)) for a,b in zip(points,points[1:])),
            "endpoint_gap":length(sub(points[0],points[-1])),"planar":planar,"planarity_deviation":deviation,
            "sampled_intersections":len(crossings),"intersection_samples":crossings[:12],
            "intersection_check":"planar polyline approximation" if planar and length(normal)>EPS else "not checked: nonplanar or collinear",
            "tangent_breaks":breaks,"tolerance":tolerance,"sampled_spans":len(points)-1,
            "bounds":[[min(p[a] for p in points) for a in range(3)],[max(p[a] for p in points) for a in range(3)]]}


def rotate(v,axis,angle):
    c,s=math.cos(angle),math.sin(angle)
    return add(add(mul(v,c),mul(cross(axis,v),s)),mul(axis,dot(axis,v)*(1-c)))


def transport(v,a,b):
    axis=cross(a,b); sine=length(axis); cosine=max(-1,min(1,dot(a,b)))
    if cosine < -1+1e-7: raise ValueError("Sweep path reverses direction; split it or soften the bend")
    return v if sine<=EPS else rotate(v,mul(axis,1/sine),math.atan2(sine,cosine))


def build_model(definition, parameters=None, alignment=None):
    """Compile named curves to a spline or quad mesh; alignment locks loft seams."""
    keys(definition,"curves output tolerance","definition")
    params=validate_parameters(parameters)
    specs=definition.get("curves")
    if not isinstance(specs,dict) or not 1<=len(specs)<=32: raise ValueError("curves needs 1-32 named definitions")
    if any(not isinstance(k,str) or not k or len(k)>64 for k in specs): raise ValueError("Curve names need 1-64 characters")
    tolerance=coordinate(definition.get("tolerance",.01),params)
    if not 1e-6<=tolerance<=1e6: raise ValueError("tolerance must be between 1e-6 and 1e6 scene units")
    curves={k:make_curve(v,params) for k,v in specs.items()}
    reports={k:curve_qa(v,tolerance) for k,v in curves.items()}
    out=definition.get("output")
    keys(out,"kind curve path profile sections path_samples profile_samples up twist scale caps align reverse","output")
    kind=out.get("kind")
    output_fields={"curve":"curve", "sweep":"path profile path_samples profile_samples up twist scale caps reverse",
                   "loft":"sections profile_samples align caps reverse"}
    if not isinstance(kind,str) or kind not in output_fields: raise ValueError("output.kind must be curve, sweep or loft")
    keys(out,"kind "+output_fields[kind],f"{kind} output")
    def get(key):
        if not isinstance(key,str) or key not in curves: raise ValueError(f"Unknown curve reference: {key!r}")
        return curves[key]
    result={"kind":kind,"curves":curves,"qa":reports,"alignment":None}
    if kind=="curve":
        result["curve"]=get(out.get("curve")); return result
    samples=integer(out.get("profile_samples",32),"profile_samples",low=3,high=256)
    caps=boolean(out.get("caps",True),"caps")
    reverse=boolean(out.get("reverse",False),"reverse")
    def profile_ok(key):
        c=get(key); report=reports[key]
        if not report["planar"] or report["sampled_intersections"]:
            raise ValueError(f"Profile {key} is nonplanar or self-intersecting")
        if caps and not c.closed: raise ValueError("Caps require closed profiles")
        if c.closed:
            ps=c.polyline(tolerance)
            area=[0.,0.,0.]
            for a,b in zip(ps,ps[1:]): area=add(area,cross(sub(a,ps[0]),sub(b,ps[0])))
            if length(area)<=EPS: raise ValueError(f"Profile {key} encloses zero area")
        return c
    if kind=="sweep":
        path=get(out.get("path")); profile=profile_ok(out.get("profile"))
        path_count=integer(out.get("path_samples",48),"path_samples",low=3 if path.closed else 2,high=512)
        if path_count*samples>50000: raise ValueError("Sweep exceeds 50000 vertices")
        pp=profile.samples(samples,tolerance)
        if any(abs(p[2])>tolerance for p in pp): raise ValueError("Sweep profile must be on world XY at z=0; its x/y are section coordinates")
        if profile.closed and sum(cross(a,b)[2] for a,b in zip(pp,pp[1:]+pp[:1]))<=EPS:
            raise ValueError("Sweep profile must wind counterclockwise in XY")
        stations=path.samples(path_count,tolerance)
        tangents=[]
        for i,p in enumerate(stations):
            before=stations[i-1] if i or path.closed else p
            after=stations[(i+1)%len(stations)] if path.closed or i<len(stations)-1 else p
            tangents.append(unit(sub(after,before)))
        up=unit(vec(out.get("up",[0,0,1]),params))
        y=sub(up,mul(tangents[0],dot(up,tangents[0])))
        if length(y)<=EPS: raise ValueError("up is parallel to the initial path tangent; choose another up direction")
        x=unit(cross(unit(y),tangents[0])); frames=[x]
        for a,b in zip(tangents,tangents[1:]):
            x=unit(transport(x,a,b)); frames.append(x)
        correction=0.
        if path.closed:
            end=transport(frames[-1],tangents[-1],tangents[0])
            correction=math.atan2(dot(tangents[0],cross(end,frames[0])),dot(end,frames[0]))
        twist=coordinate(out.get("twist",0),params)
        if abs(twist)>36000: raise ValueError("twist must be within +/-36000 degrees")
        scales=out.get("scale",[1,1])
        if not isinstance(scales,list) or len(scales)!=2: raise ValueError("scale needs [start,end]")
        start,end=[coordinate(s,params) for s in scales]
        if min(start,end)<=EPS: raise ValueError("Sweep scale must stay positive")
        if path.closed and (abs(start-end)>EPS or abs(twist/360-round(twist/360))>1e-8):
            raise ValueError("Closed sweep needs equal end scales and whole-turn twist")
        if path.closed and caps: raise ValueError("Closed sweep needs caps=false")
        sections=[]
        for i,(p,t,x) in enumerate(zip(stations,tangents,frames)):
            f=i/(path_count if path.closed else path_count-1)
            x=rotate(x,t,(math.radians(twist)+correction)*f); y=cross(t,x)
            scale=start+(end-start)*f
            sections.append([add(p,mul(add(mul(x,q[0]),mul(y,q[1])),scale)) for q in pp])
        closed_profile,closed_path=profile.closed,path.closed
    elif kind=="loft":
        names=out.get("sections")
        if not isinstance(names,list) or not 2<=len(names)<=128: raise ValueError("Loft sections needs 2-128 curve names")
        cs=[profile_ok(k) for k in names]
        if len({c.closed for c in cs})!=1: raise ValueError("Loft sections must share open/closed state")
        sections=[c.samples(samples,tolerance) for c in cs]
        # A reversed outline must not quietly generate a twisted ribbon. The
        # first section fixes winding; correspondence shifts never reverse it.
        if cs[0].closed:
            normals=[]
            for pts in sections:
                n=[0.,0.,0.]
                for a,b in zip(pts,pts[1:]+pts[:1]): n=add(n,cross(sub(a,pts[0]),sub(b,pts[0])))
                normals.append(unit(n))
            if any(dot(a,b)<=0 for a,b in zip(normals,normals[1:])):
                raise ValueError("Adjacent loft profiles reverse winding or turn at least 90 degrees; fix their planes/order")
        align=out.get("align","start")
        if align not in {"start","auto"}: raise ValueError("align must be start or auto")
        if alignment is not None and (not isinstance(alignment,list) or len(alignment)!=len(cs)):
            raise ValueError("Stored loft alignment is invalid")
        chosen=[]
        for i,pts in enumerate(sections):
            shift=0
            if alignment is not None:
                shift=integer(alignment[i],"alignment",low=0,high=samples-1)
            elif align=="auto" and i and cs[i].closed:
                previous=sections[i-1]
                a=mul([sum(p[k] for p in previous) for k in range(3)],1/samples)
                b=mul([sum(p[k] for p in pts) for k in range(3)],1/samples)
                shift=min(range(samples),key=lambda s:sum(dot(d,d) for j in range(samples) for d in [sub(sub(previous[j],a),sub(pts[(j+s)%samples],b))]))
            sections[i]=pts[shift:]+pts[:shift]; chosen.append(shift)
        result["alignment"]=chosen
        closed_profile,closed_path=cs[0].closed,False
    else: raise ValueError("output.kind must be curve, sweep or loft")
    vertices,faces,_=build_loft(sections,profile_closed=closed_profile,close_path=closed_path,caps=caps,reverse=reverse)
    result.update(vertices=vertices,faces=faces)
    result["notes"]=["Quad cage; surface intersections and thickness are not certified. Inspect the silhouette and highlights.",
                     "Sampling resolution and loft seam correspondence remain fixed during parameter updates."]
    return result
