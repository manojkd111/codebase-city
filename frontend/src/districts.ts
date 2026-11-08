// District identity: a kind (from its name) and a stable accent colour, used on curbs and labels.
// Accents avoid the risk ramp (blue→amber→red) and the agent colours so they never read as a metric.

export type DistrictKind = "source" | "tests" | "examples" | "docs" | "infra" | "root";

const KIND_RULES: [DistrictKind, RegExp][] = [
  ["tests", /(^|\/)(tests?|specs?|__tests__|testing)$/i],
  ["examples", /(^|\/)(examples?|samples?|demos?)$/i],
  ["docs", /(^|\/)(docs?|documentation|site)$/i],
  ["infra", /(^|\/)(scripts?|tools?|ci|\.github|config|deploy|infra|build|bin)$/i],
  ["root", /^\(root\)$/],
];

export function districtKind(name: string): DistrictKind {
  for (const [kind, rx] of KIND_RULES) if (rx.test(name)) return kind;
  return "source";
}

export const KIND_LABEL: Record<DistrictKind, string> = {
  source: "source",
  tests: "tests",
  examples: "examples",
  docs: "docs",
  infra: "infra",
  root: "root",
};

// Muted so curbs stay calm at a distance; source districts cycle through the first few.
const SOURCE_ACCENTS = ["#7c9cff", "#c08bff", "#4fd1c5", "#f48fb1", "#9be7a6", "#90cdf4"];
const KIND_ACCENT: Record<Exclude<DistrictKind, "source">, string> = {
  tests: "#7f8ea3",
  examples: "#e0b872",
  docs: "#d4c4a0",
  infra: "#a0aec0",
  root: "#8a94a6",
};

/** Accent per district name: stable across reloads because it depends only on the sorted names. */
export function districtAccents(names: string[]): Map<string, string> {
  const out = new Map<string, string>();
  let i = 0;
  for (const name of [...names].sort()) {
    const kind = districtKind(name);
    out.set(name, kind === "source" ? SOURCE_ACCENTS[i++ % SOURCE_ACCENTS.length] : KIND_ACCENT[kind]);
  }
  return out;
}
