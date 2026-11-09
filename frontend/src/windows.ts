import * as THREE from "three";

/**
 * Building material with windows drawn in the shader, so the city stays a single instanced mesh of
 * unit boxes (no extra geometry per building, however many files there are).
 *
 * Each instance's scale gives the building's real width and height, so the window grid is laid out
 * per facade: whole columns fitted to the face width, floors of a fixed height, a margin at the
 * ground and the roof. The glass darkens the risk colour rather than replacing it, so the colour
 * still reads as the facade's. Some windows are lit, chosen by a hash of the building's position
 * and the window's cell, so the pattern is stable frame to frame. Up close the windows have crisp
 * edges; far away, where a window is smaller than a couple of pixels, the pattern fades to its
 * average tone instead of shimmering.
 */

export const FLOOR_HEIGHT = 0.55; // world units per storey
export const WINDOW_SPACING = 0.42; // target width of one window column
export const LIT_SHARE = 0.22; // share of windows lit; more and the glow tints the risk colours

const VERTEX_DECL = /* glsl */ `
varying vec4 vCbcFacade; // x: across the face from its left edge, y: height above ground, z: face width, w: building height
varying float vCbcSide;  // 1 on walls, 0 on roof and floor
varying float vCbcSeed;  // per building and face, for which windows are lit
`;

const VERTEX_BODY = /* glsl */ `
#include <begin_vertex>
#ifdef USE_INSTANCING
  vec3 cbcScale = vec3(length(instanceMatrix[0].xyz), length(instanceMatrix[1].xyz), length(instanceMatrix[2].xyz));
  vec2 cbcBase = instanceMatrix[3].xz;
#else
  vec3 cbcScale = vec3(1.0);
  vec2 cbcBase = vec2(0.0);
#endif
vec3 cbcLocal = position * cbcScale;                // building-local units, centred on the box
float cbcAlongX = step(0.5, abs(normal.z));         // faces looking along +-z run along x
float cbcU = mix(cbcLocal.z, cbcLocal.x, cbcAlongX);
float cbcW = mix(cbcScale.z, cbcScale.x, cbcAlongX);
vCbcFacade = vec4(cbcU + cbcW * 0.5, cbcLocal.y + cbcScale.y * 0.5, cbcW, cbcScale.y);
vCbcSide = 1.0 - step(0.5, abs(normal.y));
vCbcSeed = dot(cbcBase, vec2(12.9898, 78.233)) + dot(normal, vec3(3.1, 0.0, 7.7));
`;

const FRAGMENT_DECL = /* glsl */ `
varying vec4 vCbcFacade;
varying float vCbcSide;
varying float vCbcSeed;
uniform float uCbcFloor;
uniform float uCbcSpacing;
uniform float uCbcLitShare;
`;

// Runs right after the instance (risk) colour is applied. Declares cbcWin / cbcLit at main() scope
// so the roughness and emissive steps below can use them.
const FRAGMENT_COLOR = /* glsl */ `
#include <color_fragment>
float cbcWin = 0.0;
float cbcLit = 0.0;
if (vCbcSide > 0.5) {
  float floors = floor(max(vCbcFacade.w - 0.35, 0.0) / uCbcFloor);
  float cols = max(1.0, floor(vCbcFacade.z / uCbcSpacing));
  float fx = vCbcFacade.x / (vCbcFacade.z / cols);
  float fy = (vCbcFacade.y - 0.18) / uCbcFloor;
  float inBand = step(0.0, fy) * step(fy, floors) * step(0.0, fx) * step(fx, cols);
  vec2 cell = floor(vec2(fx, fy));
  vec2 f = fract(vec2(fx, fy));
  // Edge softness in cell units, from the screen-space size of a cell: crisp up close, soft far away.
  vec2 px = max(fwidth(vec2(fx, fy)), vec2(1e-4));
  vec2 e = clamp(px * 1.5, 0.01, 0.5);
  float wx = smoothstep(0.2 - e.x, 0.2 + e.x, f.x) * (1.0 - smoothstep(0.8 - e.x, 0.8 + e.x, f.x));
  float wy = smoothstep(0.22 - e.y, 0.22 + e.y, f.y) * (1.0 - smoothstep(0.82 - e.y, 0.82 + e.y, f.y));
  float crisp = wx * wy;
  // Below ~2 px per window the shape can't be drawn cleanly; blend to the average coverage (0.6 x 0.6).
  float detail = clamp(1.6 - max(px.x, px.y) * 2.0, 0.0, 1.0);
  cbcWin = mix(0.36, crisp, detail) * inBand;
  float h = fract(sin(dot(cell, vec2(127.1, 311.7)) + vCbcSeed) * 43758.5453);
  cbcLit = mix(uCbcLitShare, step(1.0 - uCbcLitShare, h), detail) * cbcWin;
}
// Glass: a dark, slightly blue tint that keeps a trace of the facade colour.
diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb * 0.28 + vec3(0.025, 0.035, 0.055), cbcWin * 0.9);
`;

const FRAGMENT_ROUGHNESS = /* glsl */ `
#include <roughnessmap_fragment>
roughnessFactor = mix(roughnessFactor, 0.22, cbcWin);
`;

const FRAGMENT_EMISSIVE = /* glsl */ `
#include <emissivemap_fragment>
totalEmissiveRadiance += vec3(1.0, 0.76, 0.42) * cbcLit * 0.6;
`;

export function makeBuildingMaterial(): THREE.MeshStandardMaterial {
  const material = new THREE.MeshStandardMaterial({ roughness: 0.75, metalness: 0.05 });
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uCbcFloor = { value: FLOOR_HEIGHT };
    shader.uniforms.uCbcSpacing = { value: WINDOW_SPACING };
    shader.uniforms.uCbcLitShare = { value: LIT_SHARE };
    shader.vertexShader = VERTEX_DECL + shader.vertexShader.replace("#include <begin_vertex>", VERTEX_BODY);
    shader.fragmentShader =
      FRAGMENT_DECL +
      shader.fragmentShader
        .replace("#include <color_fragment>", FRAGMENT_COLOR)
        .replace("#include <roughnessmap_fragment>", FRAGMENT_ROUGHNESS)
        .replace("#include <emissivemap_fragment>", FRAGMENT_EMISSIVE);
  };
  // Keep this program separate from every other MeshStandardMaterial in the scene.
  material.customProgramCacheKey = () => "cbc-building-windows-v1";
  return material;
}
