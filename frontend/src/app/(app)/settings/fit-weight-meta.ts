/**
 * Fit-score weights — the single source of UI copy that describes each weight.
 *
 * Mirrors `DEFAULT_WEIGHTS` in `backend/app/scoring/fit_score.py`. The keys
 * MUST stay in lockstep with the backend — a drift-guard test on the backend
 * side reads this file's key list and fails if the two diverge. That test is
 * the reason this file exports `FIT_WEIGHT_KEYS` in a stable, machine-readable
 * shape (top-level `keys` array + one entry per key in `FIT_WEIGHT_META`).
 *
 * Each entry is intentionally long-form: the tooltip is the place where an
 * operator learns what the number actually does. The trigger sentence names
 * the exact keyword regex / rule so support conversations can point to code.
 */

export const DEFAULT_FIT_WEIGHTS: Record<string, number> = {
  in_region: 15,
  named_dm_direct_contact: 25,
  warehouse_or_dc: 15,
  ships_nationwide: 10,
  industry_freight_heavy: 10,
  equipment_hint: 10,
  locations_bonus_per_extra: 3,
  has_phone: 5,
  penalty_generic_email_only: -10,
}

export type FitWeightMeta = {
  /** Human-readable label shown next to the number input. */
  label: string
  /** One-line trigger summary; used as a subheading in the tooltip. */
  trigger: string
  /** Full explanation shown on hover/focus/tap. Multi-sentence. */
  hint: string
  /** Concrete worked example — must include real values. */
  example: string
  /** How this weight changes who gets auto-contacted. */
  auto_outreach_effect: string
}

export const FIT_WEIGHT_META: Record<keyof typeof DEFAULT_FIT_WEIGHTS, FitWeightMeta> = {
  in_region: {
    label: "In-region shipper",
    trigger:
      "Lead state is one of the 33 eastern US states + DC that LJM covers (IN_REGION_STATES).",
    hint:
      "Adds points when the lead's state is inside LJM's operating region. Set higher to weight geography more; set to 0 to make location stop mattering entirely.",
    example:
      "Default 15: a shipper in PA scores +15. Set 30 and it scores +30. Set 0 and PA gets no location bonus at all — a TX shipper (out of region) would score the same on this signal.",
    auto_outreach_effect:
      "Raising this bonus pushes more eastern-US leads over the min-fit threshold, so more of them become eligible for auto-contact. Lowering it narrows auto-contact to leads that qualify on other, stronger signals.",
  },
  named_dm_direct_contact: {
    label: "Named decision-maker with direct contact",
    trigger:
      "At least one contact is flagged is_decision_maker (freight-relevant title) AND has a non-generic email OR a phone.",
    hint:
      "The strongest positive signal by default. Triggers when the enrichment scraper found a named person with a freight-relevant title (logistics / transportation / warehouse / supply chain / distribution / shipping / procurement / buyer / operations / VP ops / director of logistics) whose contact is direct — a non-generic email (not info@ / hello@ / etc.) OR a phone.",
    example:
      "Default 25: Acme Corp has Jane Doe, Director of Logistics, jane.doe@acme.com → +25. Setting 40 would make one confirmed DM worth more than the in_region + warehouse signals combined.",
    auto_outreach_effect:
      "Because this is the biggest single contributor, raising it dramatically shifts auto-contact toward leads with confirmed decision-makers. Lowering it lets warehouse / industry / nationwide signals carry more weight even when the DM is unnamed.",
  },
  warehouse_or_dc: {
    label: "Warehouse / distribution center mentioned",
    trigger:
      "Site text matches /warehouse|distribution cent(er|re)|dc network|fulfillment cent(er|re)/i.",
    hint:
      "Adds points when the crawled site text mentions physical distribution infrastructure. A shipper that talks about warehouses is far more likely to have outbound freight moving than one that doesn't.",
    example:
      "Default 15: Beta Foods' About page says \"our Ohio distribution center serves the Midwest\" → +15. Set 20 and it becomes +20.",
    auto_outreach_effect:
      "Raising this is the surgical way to make brick-and-mortar shippers cross the threshold even when their DM listing is missing. Lowering it de-emphasizes site content in favor of contact-derived signals.",
  },
  ships_nationwide: {
    label: "Ships nationwide",
    trigger:
      "Site text matches /ship(s|ping)? nationwide|nationwide shipping|US-wide|coast to coast/i.",
    hint:
      "Fires when the site explicitly claims national shipping reach. A strong hint the company has consistent outbound lanes rather than one-off shipments.",
    example:
      "Default 10: Gamma Retail's homepage says \"We ship nationwide from three US warehouses\" → +10.",
    auto_outreach_effect:
      "Raise to bias auto-contact toward multi-lane shippers; lower to include regional shippers that only ship within a state or two.",
  },
  industry_freight_heavy: {
    label: "Freight-heavy industry",
    trigger:
      "Site text hits any of: retail, food, beverage, manufacturing, building materials, paper, packaging, chemicals, steel, metals, agriculture, automotive.",
    hint:
      "Adds points when the site mentions an industry known to move freight consistently. Multiple keyword hits still add the same flat bonus once — this is a category signal, not a keyword-count signal.",
    example:
      "Default 10: Delta Industrial says \"custom steel and metals for construction\" (steel + metals + building materials, all counted once) → +10.",
    auto_outreach_effect:
      "Raise to focus auto-contact on the industries LJM's fleet is best matched to; lower to broaden into sectors where the fit is less obvious from industry alone.",
  },
  equipment_hint: {
    label: "Equipment hint on site",
    trigger:
      "Site text mentions dry van, reefer, refrigerated, flatbed, LTL, or less than truckload.",
    hint:
      "Fires when the site references specific trailer types or freight modes. Even one mention proves the company thinks in freight terms — a valuable qualifier.",
    example:
      "Default 10: Epsilon Foods' shipping FAQ mentions \"reefer trucks for perishable orders\" → +10.",
    auto_outreach_effect:
      "Raise to prioritize leads whose freight needs are already articulated on their website; lower to include leads whose needs must be inferred.",
  },
  locations_bonus_per_extra: {
    label: "Multi-location bonus (per extra location)",
    trigger:
      "Adds N points per location beyond the first, capped at 3 extras (so 4+ locations still bonus at 3 extras).",
    hint:
      "Multi-location operations run more outbound freight, more predictably, than single-site ones. Each additional location adds this many points, capped at +3× the value so a 12-warehouse retailer isn't scored radically higher than a 4-warehouse one.",
    example:
      "Default 3: a shipper with 1 location → +0. 2 locations → +3. 3 → +6. 4 or more → +9 (max). Setting 5 caps at +15 for 4+ locations.",
    auto_outreach_effect:
      "Raise to bias auto-contact toward multi-DC retailers and manufacturers. Lower or set to 0 to treat single-site shippers as equals.",
  },
  has_phone: {
    label: "Phone on file",
    trigger: "Any contact row has a non-empty phone number.",
    hint:
      "A small nudge for reachability. Even a generic company phone counts here — it means outreach has a real fallback channel if email bounces.",
    example:
      "Default 5: a lead with only info@acme.com but also (555) 123-4567 on the contact page → +5.",
    auto_outreach_effect:
      "Small enough that raising or lowering rarely tips a lead across the threshold on its own, but combined with other signals it can be the deciding +5.",
  },
  penalty_generic_email_only: {
    label: "Penalty — generic email only",
    trigger:
      "Every email found is generic (info@ / hello@ / contact@ / office@ / support@ / sales@ / admin@ / hi@ / team@) AND no named decision-maker has a direct contact.",
    hint:
      "The one negative weight. Subtracts points when the only way to reach the company is a shared inbox — those addresses are the lowest-answer-rate targets and often blackholed. If a named DM with a direct contact is also present, this penalty is waived.",
    example:
      "Default −10: Zeta Corp's only listed email is info@zetacorp.com and no named DM was found → −10. If Jane Doe (VP Ops, jane@zetacorp.com) is also on file, the penalty does NOT apply.",
    auto_outreach_effect:
      "Making this more negative (e.g. −20) is the fastest way to stop auto-contact from ever emailing shared inboxes. Setting to 0 disables the penalty entirely — auto-contact will happily email info@ addresses.",
  },
}

/** Stable, sorted list of weight keys — the drift-guard test compares this to Python. */
export const FIT_WEIGHT_KEYS: readonly string[] = Object.keys(DEFAULT_FIT_WEIGHTS).sort()

/**
 * Panel-level explanation. Kept here so the copy is co-located with the per-
 * weight strings and moves as one unit.
 */
export const FIT_PANEL_INTRO = {
  headline:
    "Fit score is a deterministic 0–100 rating computed from the signals below. Every positive signal adds its weight; the one penalty subtracts. The total is clamped to 0–100 (defaults sum to a max of 99). A lead with essentially no data scores 0 with reason \"not enough data\" — never a made-up baseline.",
  worked_example:
    "Worked example — Acme Foods, PA: in-region (+15), named DM Jane Doe with direct email (+25), warehouse mentioned (+15), ships nationwide (+10), freight-heavy industry \"food\" (+10), phone on file (+5), 3 locations noted → 2 extras × 3 (+6). Total: 86. If Acme instead scored 55 and the auto-contact threshold is 60, raising warehouse_or_dc from 15 to 20 would lift Acme to 60 and make it eligible for auto-contact.",
  auto_outreach_link:
    "Weights change who gets auto-contacted: auto-contact only emails leads whose fit is at or above the \"Minimum fit\" threshold in the Auto-outreach panel. Widen a signal and more leads clear the bar; narrow it and fewer do.",
}

// -- self-check: every DEFAULT_FIT_WEIGHTS key must have a META entry ------
// This runs at import time in dev; a missing key would surface immediately in
// the Settings page during `pnpm dev`, long before it ships.
for (const k of Object.keys(DEFAULT_FIT_WEIGHTS)) {
  if (!(k in FIT_WEIGHT_META)) {
    console.warn(`FIT_WEIGHT_META missing entry for '${k}' — copy will show the raw key.`)
  }
}
