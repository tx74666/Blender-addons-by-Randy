"""Scale-aware two-link calibration geometry, independent of Blender's IK solver."""
import math
import struct


def add(a, b): return tuple(x+y for x,y in zip(a,b))
def sub(a, b): return tuple(x-y for x,y in zip(a,b))
def mul(a, s): return tuple(x*s for x in a)
def dot(a, b): return sum(x*y for x,y in zip(a,b))
def length(a): return math.sqrt(dot(a,a))
def cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def unit(a):
    n = length(a)
    if n < 1e-15: raise ValueError('Zero direction.')
    return mul(a, 1/n)
def project(a, axis): return sub(a, mul(axis, dot(a,axis)))


def stable_bend(start,joint,end,absolute_tolerance=0.):
    chord=sub(end,start);reach=length(chord)
    total=length(sub(joint,start))+length(sub(end,joint))
    if reach <= max(1e-12,absolute_tolerance):return False
    height=length(project(sub(joint,start),mul(chord,1/reach)))
    return height>max(absolute_tolerance,total*1e-4) and total-reach>max(absolute_tolerance,total*1e-6)


def solve(start, joint, end, target, *, allow_bend=False, minimum_bend=5.,
          max_shift=.1, max_length=.005, direction_tolerance=5., absolute_tolerance=0.,
          native_precision=False):
    vectors = (start, joint, end, target)
    if any(len(v) != 3 or not all(math.isfinite(x) for x in v) for v in vectors):
        raise ValueError('Calibration coordinates must be finite XYZ values.')
    values = (minimum_bend, max_shift, max_length, direction_tolerance)
    if not all(math.isfinite(v) for v in values) or not (0 < minimum_bend <= 15 and
            0 <= max_shift <= .5 and 0 <= max_length <= .05 and 0 <= direction_tolerance <= 5):
        raise ValueError('Invalid calibration limits.')
    a, b, chord = length(sub(joint,start)), length(sub(end,joint)), sub(end,start)
    total, reach = a+b, length(chord)
    if total < 1e-12 or min(a,b,reach) <= total*1e-8:
        raise ValueError('Zero bone length or coincident root/end.')
    axis = mul(chord, 1/reach)
    desired = project(target,axis)
    if length(desired) <= max(1e-12, length(target)*1e-4):
        raise ValueError('Target direction is zero or parallel to root/end.')
    desired = unit(desired)
    modeled = project(sub(joint,start),axis)
    stable = stable_bend(start,joint,end,absolute_tolerance)
    old_direction = unit(modeled) if length(modeled) > total*1e-8 else None
    angle = math.degrees(math.acos(max(-1.,min(1.,dot(old_direction,desired))))) if old_direction else None
    direction = desired
    new_a, new_b = a,b
    if not stable:
        if not allow_bend:
            raise ValueError('Direction unstable: explicitly allow a small bone length change in Custom.')
        # Fixed endpoints and fixed length ratio: only a common scale can add bend.
        factor = reach / math.sqrt(a*a+b*b+2*a*b*math.cos(math.radians(minimum_bend)))
        new_a, new_b = a*factor,b*factor
    elif angle <= direction_tolerance + 1e-7:
        direction = old_direction
    else:
        # Closest admissible plane, not an unnecessary turn to the exact target.
        turn = math.radians(angle-direction_tolerance)
        sign = -1. if dot(axis,cross(old_direction,desired)) < -1e-12 else 1.
        direction = add(mul(old_direction,math.cos(turn)),mul(cross(axis,old_direction),sign*math.sin(turn)))
        direction = unit(project(direction,axis))
    x = (new_a*new_a-new_b*new_b+reach*reach)/(2*reach)
    h2 = new_a*new_a-x*x
    if h2 < -total*total*1e-10:
        raise ValueError('Lengths cannot reach the fixed endpoints.')
    height = math.sqrt(max(0.,h2))
    center = add(start,mul(axis,x))
    candidate = add(center,mul(direction,height))
    if stable and angle <= direction_tolerance+1e-7:
        candidate = tuple(joint)
    if native_precision:
        if stable and angle > direction_tolerance+1e-7:
            # Reserve only the float32 rounding envelope inside the angular limit.
            # The bound covers every point of this circle, including translated rigs.
            rounding = length(tuple(abs(v)+height for v in center))*2**-23 + 2**-148
            margin = math.degrees(math.asin(min(1.,2*rounding/max(height,1e-30))))
            turn = math.radians(angle-max(0.,direction_tolerance-margin))
            direction = unit(project(add(mul(old_direction,math.cos(turn)),
                                         mul(cross(axis,old_direction),sign*math.sin(turn))),axis))
            candidate = add(center,mul(direction,height))
        try:
            candidate = struct.unpack('fff',struct.pack('fff',*candidate))
        except (OverflowError, struct.error) as exc:
            raise ValueError('Candidate exceeds native coordinate precision.') from exc
        # Roll and the overlay must describe the point Blender will actually store.
        direction = unit(project(sub(candidate,start),axis))
        new_a,new_b = length(sub(candidate,start)),length(sub(end,candidate))
    shift = length(sub(candidate,joint))
    changes = (new_a/a-1,new_b/b-1)
    errors = []
    if shift > total*max_shift+total*1e-7: errors.append('Joint displacement exceeds the allowed limit.')
    if max(map(abs,changes)) > max_length+1e-7: errors.append('Bone length change exceeds the allowed limit.')
    if not stable_bend(start,candidate,end,absolute_tolerance):
        errors.append('Candidate is below the solver stability tolerance at this native bone scale.')
    if native_precision and math.degrees(math.acos(max(-1.,min(1.,dot(direction,desired))))) > direction_tolerance+1e-7:
        errors.append('Native coordinates are too coarse for this bend direction; review the rig scale.')
    return {'joint': candidate, 'direction': direction, 'z': mul(unit(cross(axis,direction)),-1),
            'shift': shift, 'shift_ratio': shift/total, 'length_changes': changes,
            'direction_error_degrees': angle, 'added_bend': not stable, 'errors': errors,
            'bend_degrees': math.degrees(math.acos(max(-1.,min(1.,dot(unit(sub(joint,start)),unit(sub(end,joint))))))),
            'needs_acknowledgment': shift/total > .02 or max(map(abs,changes)) > .002}
