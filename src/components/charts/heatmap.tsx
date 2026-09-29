/** Reply-rate heatmap, weekday × send-time. Single blue ramp, value printed in each cell. */
const RAMP = ["#e8f0fb", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]

export function Heatmap({ rows }: { rows: { day: string; cells: { hour: string; sent: number; replyRate: number }[] }[] }) {
  const max = Math.max(...rows.flatMap((r) => r.cells.map((c) => c.replyRate)), 0.01)
  const hours = rows[0]?.cells.map((c) => c.hour) ?? []
  return (
    <div>
      <div className="grid gap-[2px]" style={{ gridTemplateColumns: `3rem repeat(${hours.length}, minmax(0, 1fr))` }}>
        <div />
        {hours.map((h) => (
          <div key={h} className="pb-1 text-center font-mono text-[0.7rem] text-muted-foreground">{h}h</div>
        ))}
        {rows.map((r) => (
          <div key={r.day} className="contents">
            <div className="flex items-center font-display text-sm font-semibold">{r.day}</div>
            {r.cells.map((c) => {
              const step = Math.min(RAMP.length - 1, Math.floor((c.replyRate / max) * (RAMP.length - 1)))
              const dark = step >= 4
              return (
                <div
                  key={c.hour}
                  title={`${r.day} ${c.hour}h · ${Math.round(c.replyRate * 100)}% replied · ${c.sent} emails`}
                  className="flex h-11 items-center justify-center rounded-[3px] font-mono text-xs font-semibold transition-transform hover:scale-[1.04]"
                  style={{ background: RAMP[step], color: dark ? "#fff" : "#16171a" }}
                >
                  {Math.round(c.replyRate * 100)}%
                </div>
              )
            })}
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
        <span>Low reply rate</span>
        <div className="flex gap-[2px]">
          {RAMP.map((c) => (
            <span key={c} className="h-2.5 w-5 rounded-[2px]" style={{ background: c }} />
          ))}
        </div>
        <span>High</span>
      </div>
    </div>
  )
}
