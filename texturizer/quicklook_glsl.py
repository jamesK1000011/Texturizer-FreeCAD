# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 the Texturizer for FreeCAD contributors.
# A GLSL 1.20 port of the bump-only path of sharedGLSL and the fragment
# shader in js/previewMaterial.js of BumpMesh (stlTexturizer) by Stefan
# Hermann (CNC Kitchen), AGPL-3.0, github.com/CNCKitchen/stlTexturizer.
"""Shader sources for the Quick look (design 3.4): VERTEX and FRAGMENT.

GLSL 1.20 (FreeCAD 1.1 on macOS runs a legacy OpenGL 2.1 context): no
texture(), in/out, layout, precision, loops or arrays. The per-layer code is
written once (LAYER, "@" standing for the layer number) and unrolled for the
four layers, each with its own uniforms (uMode0 ... uMap3), so nothing is
indexed dynamically. The vertex shader reads no textures.

Per layer k (all float or vecN, set by quicklook.uniforms_from_layers):
    uMode k     0 planar XY, 1 XZ, 2 YZ, 3 cylindrical (outline wrap too),
                4 spherical, 5 triplanar, 6 cubic
    uScale k    the relative tile size, aspect folded in (mapping.js
                scaleMmToRelative / textureAspect): uv = raw / uScale + uOffset
    uOffset k, uRot k (radians), uRotZ k (1 = rotate around Z)
    uAmp k      Texture height, signed (Reverse), 0 for a hidden active layer
    uSym k, uAdd k (1 = Add, 0 = Cover)
    uCyl k      cylinder centre x, y and radius
    uSeam k     mapping blend, seam band width, cap angle (degrees)
    uMap k      the height map (texture unit k)
Global: uLayerCount, uActive, uFrame (object -> texturing frame, mat4),
uBoundsMin / uBoundsSize / uBoundsCenter (in the frame).
Per vertex: gl_Color RGBA = the four layers' weights (mask x Smooth Mask
falloff x angle mask).
"""
import re

MAX_LAYERS = 4

HEADER = """#version 120
// Texturizer for FreeCAD, shader Quick look. A port of the bump-only preview
// of BumpMesh (stlTexturizer, js/previewMaterial.js) by Stefan Hermann
// (CNC Kitchen), AGPL-3.0-only, github.com/CNCKitchen/stlTexturizer.
"""

UNIFORMS_LAYER = """
uniform float uMode@;
uniform vec2  uScale@;
uniform vec2  uOffset@;
uniform float uRot@;
uniform float uRotZ@;
uniform float uAmp@;
uniform float uSym@;
uniform float uAdd@;
uniform vec3  uCyl@;
uniform vec3  uSeam@;
uniform sampler2D uMap@;
"""

UNIFORMS_GLOBAL = """
uniform float uLayerCount;
uniform float uActive;
uniform mat4  uFrame;
uniform vec3  uBoundsMin;
uniform vec3  uBoundsSize;
uniform vec3  uBoundsCenter;
"""

VARYINGS = """
varying vec3 vFramePos;
varying vec3 vFrameN;
varying vec3 vViewPos;
varying vec3 vViewN;
varying vec4 vWeights;
"""

VERTEX = HEADER + "uniform mat4  uFrame;\n" + VARYINGS + """
void main() {
    vFramePos = (uFrame * gl_Vertex).xyz;
    vec3 n = mat3(uFrame) * gl_Normal;
    vFrameN = length(n) > 1e-6 ? normalize(n) : vec3(0.0, 0.0, 1.0);
    vViewPos = (gl_ModelViewMatrix * gl_Vertex).xyz;
    vViewN = gl_NormalMatrix * gl_Normal;
    vWeights = gl_Color;
    gl_Position = ftransform();
}
"""

SHARED = """
const float PI = 3.14159265358979;
const float TWO_PI = 6.28318530717959;
const float CUBIC_AXIS_EPSILON = 1e-4;

float smooth01(float t) { t = clamp(t, 0.0, 1.0); return t * t * (3.0 - 2.0 * t); }

vec3 oneHotAxis(vec3 n) {
    vec3 a = abs(n);
    if (a.x >= a.y - CUBIC_AXIS_EPSILON && a.x >= a.z - CUBIC_AXIS_EPSILON) return vec3(1.0, 0.0, 0.0);
    if (a.y >= a.z - CUBIC_AXIS_EPSILON) return vec3(0.0, 1.0, 0.0);
    return vec3(0.0, 0.0, 1.0);
}

// mapping.js getCubicBlendWeights / sharedGLSL cubicBlendWeights
vec3 cubicBlendWeights(vec3 n, float mappingBlend, float seamBandWidth) {
    vec3 a = abs(n);
    vec3 oneHot = oneHotAxis(n);
    if (mappingBlend < 0.001) return oneHot;
    float primary = dot(a, oneHot);
    float secondary = oneHot.x > 0.5 ? max(a.y, a.z) : (oneHot.y > 0.5 ? max(a.x, a.z) : max(a.x, a.y));
    float seamWidth = max(seamBandWidth, CUBIC_AXIS_EPSILON * 2.0);
    float raw = 1.0 - clamp((primary - secondary) / seamWidth, 0.0, 1.0);
    float seamMix = mappingBlend * raw * raw * (3.0 - 2.0 * raw);
    if (seamMix <= 0.001) return oneHot;
    float power = 1.0 + (1.0 - seamMix) * 11.0;
    vec3 soft = pow(a, vec3(power));
    soft /= dot(soft, vec3(1.0)) + 1e-6;
    vec3 b = mix(oneHot, soft, seamMix);
    return b / (dot(b, vec3(1.0)) + 1e-6);
}
"""

# One layer: "@" is the layer number. Every texture2D runs unconditionally
# or under a uniform branch, so the implicit derivatives stay defined.
LAYER = """
float samp@(vec2 raw, float ang) {
    vec2 uv = raw / uScale@ + uOffset@;
    float c = cos(ang); float s = sin(ang);
    uv -= 0.5;
    uv = vec2(c * uv.x - s * uv.y, s * uv.x + c * uv.y) + 0.5;
    return texture2D(uMap@, uv).r;
}

// The atan2-wrap seam cross-fade (cylindrical, spherical).
float seam@(float uRaw, float v, float band) {
    float d = uRaw < 0.5 ? uRaw : uRaw - 1.0;
    float t = smooth01((d + band) / (2.0 * max(band, 1e-6)));
    float hL = samp@(vec2(1.0 + d, v), uRot@);
    float hR = samp@(vec2(d, v), uRot@);
    float hS = samp@(vec2(uRaw, v), uRot@);
    bool zone = band > 0.001 && min(uRaw, 1.0 - uRaw) < band;
    return zone ? mix(hL, hR, t) : hS;
}

// sharedGLSL computeHeightAtPoint, tiled layers: raw height 0..1.
float height@(vec3 pos, vec3 projN, vec3 blendN) {
    float m = uMode@;
    float md = max(max(uBoundsSize.x, max(uBoundsSize.y, uBoundsSize.z)), 1e-4);
    vec3 r0 = pos - uBoundsMin;
    if (m < 0.5) return samp@(r0.xy / md, uRot@);
    if (m < 1.5) return samp@(r0.xz / md, uRot@);
    if (m < 2.5) return samp@(r0.yz / md, uRot@);
    if (m < 3.5) {
        vec2 c2 = pos.xy - uCyl@.xy;
        float C = TWO_PI * max(uCyl@.z, 1e-4);
        float uc = atan(c2.y, c2.x) / TWO_PI + 0.5;
        float hSide = seam@(uc, r0.z / C, uSeam@.y * 0.1);
        float thr = cos(radians(uSeam@.z));
        float hb = uSeam@.y * 0.5;
        float capW = smoothstep(thr - hb, thr + hb, abs(blendN.z));
        float hCap = samp@(c2 / C + 0.5, uRot@);
        return uSeam@.x < 0.001 ? hSide : mix(hSide, hCap, capW);
    }
    if (m < 4.5) {
        vec3 rel = pos - uBoundsCenter;
        float r = length(rel);
        float phi = acos(clamp(rel.z / max(r, 1e-4), -1.0, 1.0));
        return seam@(atan(rel.y, rel.x) / TWO_PI + 0.5, phi / PI, uSeam@.y * 0.1);
    }
    // triplanar / cubic: the side projections, turned around Z with uRotZ
    vec3 pN = projN;
    vec3 bN = blendN;
    vec2 sideXY = r0.xy;
    float sideAngle = uRot@;
    if (uRotZ@ > 0.5) {
        float c = cos(uRot@); float s = sin(uRot@);
        mat2 R = mat2(c, s, -s, c);
        pN.xy = R * pN.xy;
        bN.xy = R * bN.xy;
        sideXY = R * (pos.xy - uBoundsCenter.xy) + 0.5 * uBoundsSize.xy;
        sideAngle = 0.0;
    }
    float yzU = sideXY.y / md;
    if (pN.x < 0.0) yzU = -yzU;
    float xzU = sideXY.x / md;
    if (pN.y > 0.0) xzU = -xzU;
    float xyU = r0.x / md;
    if (pN.z < 0.0) xyU = -xyU;
    float hXY = samp@(vec2(xyU, r0.y / md), uRot@);
    float hXZ = samp@(vec2(xzU, r0.z / md), sideAngle);
    float hYZ = samp@(vec2(yzU, r0.z / md), sideAngle);
    vec3 w;
    if (m < 5.5) {
        w = pow(abs(pN), vec3(4.0));
        w /= dot(w, vec3(1.0)) + 1e-4;
    } else {
        vec3 a = abs(pN);
        float p1 = max(a.x, max(a.y, a.z));
        float p2 = a.x + a.y + a.z - p1 - min(a.x, min(a.y, a.z));
        w = cubicBlendWeights(p1 - p2 <= CUBIC_AXIS_EPSILON ? pN : bN, uSeam@.x, uSeam@.y);
    }
    return hYZ * w.x + hXZ * w.y + hXY * w.z;
}

// bumpLayer: the layer's screen-space height gradient, folded in with Cover / Add.
void bump@(vec3 PN, float wl, inout float dhx, inout float dhy, inout float cover) {
    float h = height@(vFramePos, PN, vFrameN);
    float gx = dFdx(h) * uAmp@;
    float gy = dFdy(h) * uAmp@;
    if (uAdd@ > 0.5) {
        dhx += gx * wl; dhy += gy * wl; cover += wl;
    } else {
        dhx = dhx * (1.0 - wl) + gx * wl;
        dhy = dhy * (1.0 - wl) + gy * wl;
        cover = cover * (1.0 - wl) + wl;
    }
}
"""

MAIN = """
vec3 lit(vec3 base, vec3 n, vec3 L, vec3 V) {
    vec3 L2 = normalize(vec3(-0.5, -0.2, -0.6));
    float d1 = max(dot(n, L), 0.0);
    float d2 = max(dot(n, L2), 0.0) * 0.35;
    float spec = pow(max(dot(n, normalize(L + V)), 0.0), 64.0) * 0.60;
    return base * 0.55
         + base * d1 * gl_LightSource[0].diffuse.rgb * vec3(1.00, 0.96, 0.88) * 0.55
         + base * d2 * vec3(0.80, 0.60, 0.50) * 0.15
         + vec3(spec) * gl_LightSource[0].specular.rgb;
}

void main() {
    float front = gl_FrontFacing ? 1.0 : -1.0;
    vec3 N = (length(vViewN) > 1e-6 ? normalize(vViewN) : vec3(0.0, 0.0, 1.0)) * front;
    vec3 dpx = dFdx(vFramePos);
    vec3 dpy = dFdy(vFramePos);
    vec3 fN = cross(dpx, dpy);
    vec3 PN = length(fN) > 1e-10 ? normalize(fN) : vFrameN;
    vec4 w = clamp(vWeights, 0.0, 1.0);

    float dhx = 0.0; float dhy = 0.0; float cover = 0.0;
    bump0(PN, w.x, dhx, dhy, cover);
    if (uLayerCount > 1.5) bump1(PN, w.y, dhx, dhy, cover);
    if (uLayerCount > 2.5) bump2(PN, w.z, dhx, dhy, cover);
    if (uLayerCount > 3.5) bump3(PN, w.w, dhx, dhy, cover);
    cover = clamp(cover, 0.0, 1.0);

    vec3 dp1 = dFdx(vViewPos);
    vec3 dp2 = dFdy(vViewPos);
    vec3 T = dp1 - dot(dp1, N) * N;
    vec3 B = dp2 - dot(dp2, N) * N;
    T = length(T) > 1e-5 ? normalize(T) : vec3(1.0, 0.0, 0.0);
    B = length(B) > 1e-5 ? normalize(B) : vec3(0.0, 1.0, 0.0);
    float bumpStr = 6.0 / max(length(dp1) + length(dp2), 1e-6);
    vec3 bv = N - bumpStr * (dhx * T + dhy * B);
    vec3 bumpN = length(bv) > 1e-6 ? normalize(bv) : N;
    bumpN = normalize(mix(N, bumpN, cover));

    vec4 lp = gl_LightSource[0].position;
    vec3 L = lp.w == 0.0 ? normalize(lp.xyz) : normalize(lp.xyz - vViewPos);
    vec3 V = normalize(-vViewPos);
    vec3 teal = vec3(0.22, 0.68, 0.68);
    vec3 maskColor = uLayerCount > 1.5 ? vec3(0.55, 0.57, 0.59) : vec3(0.85, 0.40, 0.15);
    float a = uActive;
    float activeW = a < 0.5 ? w.x : (a < 1.5 ? w.y : (a < 2.5 ? w.z : w.w));
    vec3 color = mix(lit(teal, bumpN, L, V), lit(maskColor, bumpN, L, V), 1.0 - activeW);
    gl_FragColor = vec4(color, 1.0);
}
"""


def _layers(text):
    return "".join(text.replace("@", str(k)) for k in range(MAX_LAYERS))


FRAGMENT = HEADER + _layers(UNIFORMS_LAYER) + UNIFORMS_GLOBAL + VARYINGS + SHARED + _layers(LAYER) + MAIN


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)
