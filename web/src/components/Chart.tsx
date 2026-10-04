import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Download, X } from "lucide-react";
import type { ChartResult } from "../types";
import { exportChartPng } from "../export";
import { displayLabel, issueLabel } from "../labels";

type Point = ChartResult["series"][number]["points"][number];
type Series = ChartResult["series"][number];
type Datum = Record<string, unknown> & {
  axis: string | number;
  label: string;
  points: Record<string, Point>;
};
type Kind = "line" | "bar" | "horizontal" | "donut";

interface Props {
  chart: ChartResult;
  kind: Kind;
  color?: string;
  period: string;
  fetchedAt: string;
  compact?: boolean;
  showAxes?: boolean;
  exportable?: boolean;
}

const COLORS = [
  "#169b88",
  "#4785d6",
  "#9a6ad6",
  "#d48a35",
  "#e0647f",
  "#58a45e",
  "#599bb5",
  "#bb7066",
  "#8882cf",
  "#a58b3d",
];
const numeric = (value: number | null | undefined) =>
  typeof value === "number" && Number.isFinite(value);
const valueLabel = (value: unknown, metric = "") =>
  typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("ko-KR", {
        maximumFractionDigits: /e1rm/i.test(metric) ? 1 : 2,
      }).format(value)
    : "—";

function colorFor(key: string): string {
  let hash = 0;
  for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return COLORS[hash % COLORS.length];
}

function originalUrl(id: string): string | undefined {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    id,
  )
    ? `https://www.notion.so/${id.replace(/-/g, "")}`
    : undefined;
}

function dateNumber(value: string): number {
  const date = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return date
    ? Date.UTC(Number(date[1]), Number(date[2]) - 1, Number(date[3]))
    : NaN;
}

function dateLabel(value: number): string {
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "UTC",
    month: "2-digit",
    day: "2-digit",
  }).format(value);
}

function dateOf(point: Point): string | undefined {
  return (
    point.date ?? (point as Point & { date_or_bucket?: string }).date_or_bucket
  );
}

function mergePoints(series: Series[], dated: boolean): Datum[] {
  const rows = new Map<string, Datum>();
  series.forEach((item, seriesIndex) =>
    item.points.forEach((point, pointIndex) => {
      const date = dateOf(point);
      const axis =
        dated && date
          ? dateNumber(date)
          : (point.category_id ?? date ?? point.label ?? String(pointIndex));
      if (dated && !Number.isFinite(axis)) return;
      const key = String(axis);
      let row = rows.get(key);
      if (!row) {
        row = {
          axis,
          label: displayLabel(point.label ?? date ?? String(axis)),
          points: {},
        };
        rows.set(key, row);
      }
      row[`s${seriesIndex}`] = numeric(point.value) ? point.value : null;
      row.points[`s${seriesIndex}`] = point;
    }),
  );
  return Array.from(rows.values()).sort((a, b) =>
    dated ? Number(a.axis) - Number(b.axis) : 0,
  );
}

function PointTooltip({
  active,
  payload,
  label,
  series,
  unit,
  metric,
  onClose,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{
    dataKey?: unknown;
    name?: unknown;
    value?: unknown;
    color?: string;
    payload?: Datum;
  }>;
  label?: unknown;
  series: Series[];
  unit: string;
  metric: string;
  onClose: () => void;
}) {
  if (!active || !payload?.length) return null;
  const title =
    payload[0]?.payload?.label ??
    (typeof label === "number" ? dateLabel(label) : String(label ?? ""));
  return (
    <div
      className="chart-tooltip"
      style={{
        background: "var(--surface)",
        color: "var(--text)",
        border: "1px solid var(--border)",
        padding: "10px 12px",
        borderRadius: 12,
        maxWidth: 260,
        boxShadow: "0 4px 20px #0002",
        pointerEvents: "auto",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
        }}
      >
        <strong>{title}</strong>
        <button
          type="button"
          className="icon-button"
          aria-label="툴팁 닫기"
          onClick={onClose}
        >
          <X size={16} />
        </button>
      </div>
      {payload.map((entry, index) => {
        const key = String(entry.dataKey ?? "");
        const point = entry.payload?.points?.[key];
        const item = series[Number(key.slice(1))];
        return (
          <div key={`${key}-${index}`} style={{ marginTop: 6 }}>
            <span style={{ color: entry.color }}>
              {displayLabel(item?.label ?? String(entry.name ?? ""))}:{" "}
            </span>
            <strong>
              {valueLabel(entry.value, metric)}
              {unit ? ` ${unit}` : ""}
            </strong>
            {item?.observation_only && (
              <small style={{ display: "block" }}>조건 미확인 · 관측값</small>
            )}
            {point && (
              <small style={{ display: "block" }}>
                표본 {point.sample_count ?? point.source_set_ids.length} · 세트{" "}
                {point.source_set_ids.length} · 세션{" "}
                {point.source_session_ids.length}
              </small>
            )}
            {point?.source_set_ids
              .filter((id) => originalUrl(id))
              .slice(0, 2)
              .map((id, index) => (
                <a
                  key={id}
                  href={originalUrl(id)}
                  target="_blank"
                  rel="noreferrer"
                  style={{ marginRight: 8 }}
                >
                  원본 세트 {index + 1}
                </a>
              ))}
            {point?.detail?.fixed_load !== undefined && (
              <small style={{ display: "block" }}>
                중량 {valueLabel(point.detail.fixed_load)} kg
              </small>
            )}
            {point?.detail?.partial_week === true && (
              <small style={{ display: "block" }}>
                부분 주 · {String(point.detail.range_start)} –{" "}
                {String(point.detail.range_end)}
              </small>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function DataChart({
  chart,
  kind,
  color,
  period,
  fetchedAt,
  compact = false,
  showAxes = false,
  exportable = true,
}: Props) {
  const root = useRef<HTMLDivElement>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [coarse, setCoarse] = useState(false);
  const [tooltipHidden, setTooltipHidden] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  useEffect(() => {
    const query = window.matchMedia("(pointer: coarse)");
    const update = () => setCoarse(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    setHidden(new Set());
    setTooltipHidden(false);
  }, [chart.metric, chart.title]);

  const series = chart.series;
  const dated =
    kind === "line" &&
    series.some((item) => item.points.some((point) => Boolean(dateOf(point))));
  const rows = useMemo(() => mergePoints(series, dated), [series, dated]);
  const visibleRows = rows.filter((row) =>
    series.some(
      (item, index) =>
        !hidden.has(item.key) && numeric(row[`s${index}`] as number | null),
    ),
  );
  const observation = series.some((item) => item.observation_only);
  const hasValues = series.some((item) =>
    item.points.some((point) => numeric(point.value)),
  );
  const donut =
    kind === "donut"
      ? (series[0]?.points ?? [])
          .filter((point) => numeric(point.value))
          .map((point, index) => ({
            key: point.category_id ?? point.label ?? String(index),
            label: displayLabel(point.label ?? point.category_id ?? "미상"),
            value: point.value as number,
            point,
          }))
      : [];
  const visibleDonut = donut.filter((point) => !hidden.has(point.key));
  const donutTotal = visibleDonut.reduce((sum, point) => sum + point.value, 0);
  const legends =
    kind === "donut"
      ? donut.map((point) => ({
          key: point.key,
          label: point.label,
          color: colorFor(point.key),
        }))
      : series.map((item, index) => ({
          key: item.key,
          label: displayLabel(item.label),
          color: index === 0 && color ? color : colorFor(item.key),
        }));
  const dateValues = rows
    .map((row) => Number(row.axis))
    .filter(Number.isFinite);
  const extent = dateValues.length
    ? [Math.min(...dateValues), Math.max(...dateValues)]
    : [0, 1];
  const lineDomain: [number, number] =
    extent[0] === extent[1]
      ? [extent[0] - 86_400_000, extent[1] + 86_400_000]
      : [extent[0], extent[1]];
  const toggle = (key: string) =>
    setHidden((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const save = async () => {
    const svg = root.current?.querySelector(
      "svg.recharts-surface",
    ) as SVGSVGElement | null;
    if (!svg) return;
    setExporting(true);
    setExportError("");
    try {
      await exportChartPng(svg, {
        title: chart.title,
        period,
        fetchedAt,
        unit: chart.unit,
        aggregation: chart.aggregation,
        legends: legends
          .filter((legend) => !hidden.has(legend.key))
          .map((legend) => ({
            label:
              legend.label +
              (series.find((item) => item.key === legend.key)?.observation_only
                ? " · 관측값"
                : ""),
            color: legend.color,
          }))
          .concat(
            hidden.size
              ? [
                  {
                    label: `숨긴 시리즈: ${legends
                      .filter((legend) => hidden.has(legend.key))
                      .map((legend) => legend.label)
                      .join(", ")}`,
                    color: "#888888",
                  },
                ]
              : [],
          ),
      });
    } catch (error) {
      setExportError(
        error instanceof Error ? error.message : "PNG 저장에 실패했습니다.",
      );
    } finally {
      setExporting(false);
    }
  };

  const tooltip = (
    <Tooltip
      trigger={coarse ? "click" : "hover"}
      active={tooltipHidden ? false : undefined}
      cursor={kind === "line" ? false : { fill: "var(--hover)" }}
      wrapperStyle={{ zIndex: 20, pointerEvents: "auto" }}
      content={(props) => (
        <PointTooltip
          {...props}
          series={series}
          unit={chart.unit}
          metric={chart.metric}
          onClose={() => setTooltipHidden(true)}
        />
      )}
    />
  );
  const height = compact
    ? 110
    : kind === "horizontal"
      ? Math.min(340, Math.max(200, rows.length * 29))
      : 235;
  const stroke = "var(--border)";
  const text = "var(--muted)";
  const plot = (
    <ResponsiveContainer width="100%" height="100%" minWidth={0}>
      {kind === "line" ? (
        <LineChart
          data={rows}
          margin={{
            top: compact ? 6 : 12,
            right: compact ? 6 : 14,
            bottom: 0,
            left: 0,
          }}
          accessibilityLayer
        >
          {!compact && (
            <CartesianGrid
              vertical={false}
              stroke={stroke}
              strokeDasharray="3 5"
            />
          )}
          <XAxis
            dataKey="axis"
            type={dated ? "number" : "category"}
            scale={dated ? "time" : "auto"}
            domain={dated ? lineDomain : undefined}
            tickFormatter={dated ? dateLabel : undefined}
            tick={{ fill: text, fontSize: compact ? 9 : 11 }}
            axisLine={false}
            tickLine={false}
            minTickGap={compact ? 12 : 24}
            hide={compact && !showAxes}
          />
          <YAxis
            width={compact ? (showAxes ? 32 : 20) : 46}
            tick={{ fill: text, fontSize: 11 }}
            tickFormatter={(value) => valueLabel(value, chart.metric)}
            axisLine={false}
            tickLine={false}
            domain={["auto", "auto"]}
            hide={compact && !showAxes}
          />
          {tooltip}
          {series.map((item, index) => (
            <Line
              key={item.key}
              type="linear"
              dataKey={`s${index}`}
              name={item.label}
              hide={hidden.has(item.key)}
              stroke={index === 0 && color ? color : colorFor(item.key)}
              strokeWidth={compact ? 2 : 2.5}
              strokeDasharray={item.observation_only ? "5 4" : undefined}
              connectNulls={item.points.every((point) => numeric(point.value))}
              dot={{ r: compact ? 2.5 : 4, strokeWidth: 1.5 }}
              activeDot={{ r: 6 }}
              isAnimationActive={false}
            >
              {item.points.filter((point) => numeric(point.value)).length ===
                1 && (
                <LabelList
                  dataKey={`s${index}`}
                  position="top"
                  fill="var(--text)"
                  fontSize={11}
                  formatter={(value) => valueLabel(value, chart.metric)}
                />
              )}
            </Line>
          ))}
        </LineChart>
      ) : kind === "donut" ? (
        <PieChart accessibilityLayer>
          <Pie
            data={visibleDonut}
            dataKey="value"
            nameKey="label"
            innerRadius="58%"
            outerRadius="84%"
            paddingAngle={visibleDonut.length > 1 ? 3 : 0}
            stroke="var(--surface)"
            strokeWidth={3}
            isAnimationActive={false}
          >
            {visibleDonut.map((point) => (
              <Cell key={point.key} fill={colorFor(point.key)} />
            ))}
          </Pie>
          <text
            x="50%"
            y="47%"
            textAnchor="middle"
            dominantBaseline="middle"
            fill="var(--text)"
            fontSize={30}
            fontWeight={700}
          >
            {valueLabel(donutTotal)}
          </text>
          <text
            x="50%"
            y="59%"
            textAnchor="middle"
            dominantBaseline="middle"
            fill={text}
            fontSize={12}
          >
            {chart.unit}
            {hidden.size ? " · 표시 합계" : " 합계"}
          </text>
          <Tooltip
            trigger={coarse ? "click" : "hover"}
            active={tooltipHidden ? false : undefined}
            content={({ active, payload }) =>
              active && payload?.length ? (
                <div
                  className="chart-tooltip"
                  style={{
                    background: "var(--surface)",
                    color: "var(--text)",
                    padding: 12,
                    border: "1px solid var(--border)",
                    borderRadius: 12,
                  }}
                >
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="툴팁 닫기"
                    onClick={() => setTooltipHidden(true)}
                  >
                    <X size={16} />
                  </button>
                  <strong>
                    {String(payload[0].name)} · {valueLabel(payload[0].value)}{" "}
                    {chart.unit}
                  </strong>
                  <div>
                    {donutTotal > 0
                      ? `${((Number(payload[0].value) / donutTotal) * 100).toFixed(1)}%`
                      : "0%"}{" "}
                    · {hidden.size ? "표시 항목 기준" : "전체 기준"}
                  </div>
                </div>
              ) : null
            }
          />
        </PieChart>
      ) : (
        <BarChart
          data={rows}
          layout={kind === "horizontal" ? "vertical" : "horizontal"}
          margin={{
            top: 8,
            right: kind === "horizontal" ? 40 : 18,
            bottom: 0,
            left: kind === "horizontal" ? 0 : -12,
          }}
          accessibilityLayer
        >
          <CartesianGrid
            vertical={kind === "horizontal"}
            horizontal={kind !== "horizontal"}
            stroke={stroke}
            strokeDasharray="3 5"
          />
          <XAxis
            dataKey={kind === "horizontal" ? undefined : "axis"}
            type={kind === "horizontal" ? "number" : "category"}
            domain={kind === "horizontal" ? [0, "auto"] : undefined}
            allowDecimals={!["회", "세트"].includes(chart.unit)}
            tick={{ fill: text, fontSize: 11 }}
            tickFormatter={
              kind === "horizontal"
                ? (value) => valueLabel(value, chart.metric)
                : (value) => {
                    const label =
                      rows.find((row) => row.axis === value)?.label ??
                      String(value);
                    return /^\d{4}-\d{2}-\d{2}$/.test(label)
                      ? dateLabel(dateNumber(label))
                      : label.length > 7
                        ? `${label.slice(0, 6)}…`
                        : label;
                  }
            }
            axisLine={false}
            tickLine={false}
            minTickGap={16}
          />
          <YAxis
            dataKey={kind === "horizontal" ? "axis" : undefined}
            type={kind === "horizontal" ? "category" : "number"}
            domain={kind === "horizontal" ? undefined : [0, "auto"]}
            allowDecimals={!["회", "세트"].includes(chart.unit)}
            width={kind === "horizontal" ? 95 : 46}
            interval={kind === "horizontal" ? 0 : undefined}
            tick={{ fill: text, fontSize: 11 }}
            tickFormatter={
              kind === "horizontal"
                ? (value) => {
                    const label =
                      rows.find((row) => row.axis === value)?.label ??
                      String(value);
                    return label.length > 10
                      ? `${label.slice(0, 9)}…`
                      : label.length > 7
                        ? `${label.slice(0, 6)}…`
                        : label;
                  }
                : (value) => valueLabel(value, chart.metric)
            }
            axisLine={false}
            tickLine={false}
          />
          {tooltip}
          {series.map((item, index) => (
            <Bar
              key={item.key}
              dataKey={`s${index}`}
              name={item.label}
              hide={hidden.has(item.key)}
              stackId={
                kind === "bar" && chart.metric === "sets" && series.length > 1
                  ? "set-types"
                  : undefined
              }
              fill={index === 0 && color ? color : colorFor(item.key)}
              radius={kind === "horizontal" ? [0, 5, 5, 0] : [5, 5, 0, 0]}
              maxBarSize={kind === "horizontal" ? 20 : 30}
              isAnimationActive={false}
            >
              {kind === "horizontal" && series.length === 1 && (
                <LabelList
                  dataKey={`s${index}`}
                  position="right"
                  fill="var(--muted)"
                  fontSize={11}
                  formatter={(value) => valueLabel(value, chart.metric)}
                />
              )}
              {series.length === 1 &&
                rows.map((row) => (
                  <Cell
                    key={String(row.axis)}
                    fill={
                      color ??
                      colorFor(
                        /^\d{4}-\d{2}-\d{2}$/.test(String(row.axis))
                          ? item.key
                          : String(row.axis),
                      )
                    }
                  />
                ))}
            </Bar>
          ))}
        </BarChart>
      )}
    </ResponsiveContainer>
  );

  return (
    <div
      className={`data-chart${compact ? " data-chart-compact" : ""}`}
      ref={root}
    >
      {!compact && (
        <div
          className="chart-toolbar"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
          }}
        >
          <h3>{chart.title}</h3>
          {exportable && chart.status === "ok" && hasValues && (
            <button
              type="button"
              className="icon-button"
              title={`${chart.title} PNG 저장`}
              aria-label={`${chart.title} PNG 저장`}
              disabled={exporting}
              onClick={save}
            >
              <Download size={17} />
            </button>
          )}
        </div>
      )}
      {chart.status !== "ok" || !hasValues ? (
        <div
          className="chart-empty"
          role="status"
          style={{
            minHeight: compact ? 80 : 180,
            display: "grid",
            placeItems: "center",
            padding: 16,
            color: "var(--muted)",
            textAlign: "center",
          }}
        >
          {chart.message ??
            (chart as ChartResult & { reason?: string }).reason ??
            (chart.status === "unavailable"
              ? "계산에 필요한 조건이나 기준을 확인하세요."
              : "이 기간에 기록 없음")}
        </div>
      ) : (
        <>
          <div
            className="chart-visual"
            role="img"
            aria-label={`${chart.title}. ${period}. 단위 ${chart.unit}. 아래 수치 목록에서 모든 값을 확인할 수 있습니다.`}
            style={{
              height: compact ? undefined : height,
              minWidth: 0,
              position: "relative",
            }}
            onPointerDown={() => setTooltipHidden(false)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setTooltipHidden(true);
            }}
          >
            {visibleRows.length || visibleDonut.length ? (
              plot
            ) : (
              <div
                className="chart-empty"
                style={{
                  height: "100%",
                  display: "grid",
                  placeItems: "center",
                }}
              >
                모든 시리즈가 숨겨져 있습니다.
              </div>
            )}
          </div>
          {((!compact && legends.length > 1) || kind === "donut") && (
            <div
              className="chart-legend"
              style={{ display: "flex", flexWrap: "wrap", gap: "4px 8px" }}
            >
              {legends.map((legend) => (
                <button
                  key={legend.key}
                  type="button"
                  className="legend-toggle"
                  aria-pressed={!hidden.has(legend.key)}
                  onClick={() => toggle(legend.key)}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    minHeight: 44,
                    background: "transparent",
                    border: 0,
                    fontSize: 12,
                    color: "var(--muted)",
                    opacity: hidden.has(legend.key) ? 0.45 : 1,
                    textDecoration: hidden.has(legend.key)
                      ? "line-through"
                      : undefined,
                  }}
                >
                  <span
                    aria-hidden="true"
                    style={{
                      width: 9,
                      height: 9,
                      borderRadius: kind === "donut" ? "50%" : 2,
                      background: legend.color,
                    }}
                  />
                  {legend.label}
                  {kind === "donut" &&
                    (() => {
                      const point = donut.find(
                        (item) => item.key === legend.key,
                      );
                      const total =
                        chart.denominator ??
                        donut.reduce((sum, item) => sum + item.value, 0);
                      return point
                        ? ` ${valueLabel(point.value)}${chart.unit} (${total > 0 ? ((point.value / total) * 100).toFixed(1) : "0"}%)`
                        : "";
                    })()}
                </button>
              ))}
            </div>
          )}
          {hidden.size > 0 && (
            <small className="chart-caption">
              숨김:{" "}
              {legends
                .filter((legend) => hidden.has(legend.key))
                .map((legend) => legend.label)
                .join(", ")}
            </small>
          )}
        </>
      )}
      {!compact && (
        <p className="chart-caption">
          {period} · {chart.unit || "단위 없음"} · {chart.aggregation}
        </p>
      )}
      {observation && !compact && (
        <p className="chart-caption">점선: 조건 미확인 · 관측 추세</p>
      )}
      {chart.exclusions?.count > 0 && !compact && (
        <p className="chart-caption">
          제외 {chart.exclusions.count}건
          {Array.isArray(chart.exclusions.reasons)
            ? ` · ${chart.exclusions.reasons.map(issueLabel).join(" · ")}`
            : Object.entries(chart.exclusions.reasons ?? {})
                .map(([reason, count]) => ` · ${issueLabel(reason)} ${count}`)
                .join("")}
        </p>
      )}
      {exportError && (
        <p role="alert" className="error-text">
          {exportError}
        </p>
      )}
      {hasValues && (
        <details className="chart-values">
          <summary
            style={{
              minHeight: 36,
              display: "flex",
              alignItems: "center",
              cursor: "pointer",
              fontSize: 12,
              color: "var(--muted)",
            }}
          >
            수치 목록
          </summary>
          <div style={{ maxHeight: 260, overflow: "auto" }}>
            {series.map((item) => (
              <div key={item.key}>
                <strong style={{ fontSize: 12 }}>
                  {displayLabel(item.label)}
                  {item.observation_only ? " · 관측값" : ""}
                  {hidden.has(item.key) ? " · 차트에서 숨김" : ""}
                </strong>
                <ul
                  style={{
                    margin: "6px 0 12px",
                    paddingLeft: 20,
                    fontSize: 12,
                  }}
                >
                  {item.points.map((point, index) => (
                    <li
                      key={`${dateOf(point) ?? point.category_id ?? index}-${index}`}
                    >
                      {displayLabel(
                        point.label ??
                          dateOf(point) ??
                          point.category_id ??
                          `항목 ${index + 1}`,
                      )}
                      : {valueLabel(point.value, chart.metric)} {chart.unit} ·
                      표본 {point.sample_count ?? point.source_set_ids.length}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
