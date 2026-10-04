export interface ChartExportMetadata {
  title: string;
  period: string;
  fetchedAt: string;
  unit: string;
  aggregation?: string;
  legends?: Array<string | { label: string; color: string }>;
}

const SVG_STYLES = [
  "fill",
  "fill-opacity",
  "stroke",
  "stroke-width",
  "stroke-opacity",
  "stroke-dasharray",
  "opacity",
  "font-family",
  "font-size",
  "font-weight",
  "text-anchor",
  "dominant-baseline",
  "letter-spacing",
  "color",
] as const;

function backgroundOf(element: Element): string {
  let current: Element | null = element;
  while (current) {
    const color = getComputedStyle(current).backgroundColor;
    if (color && color !== "transparent" && color !== "rgba(0, 0, 0, 0)")
      return color;
    current = current.parentElement;
  }
  return "#ffffff";
}

function wrapText(
  context: CanvasRenderingContext2D,
  text: string,
  width: number,
): string[] {
  const lines: string[] = [];
  let current = "";
  for (const char of text) {
    if (current && context.measureText(current + char).width > width) {
      lines.push(current);
      current = char;
    } else current += char;
  }
  if (current) lines.push(current);
  return lines;
}

function fetchedLabel(value: string): string {
  const time = new Date(value);
  if (!Number.isFinite(time.getTime())) return value;
  return `${new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(time)} 조회 · Asia/Seoul`;
}

/** Export the rendered SVG at 2× resolution, with the currently visible theme. */
export async function exportChartPng(
  svg: SVGSVGElement,
  metadata: ChartExportMetadata,
): Promise<void> {
  await document.fonts.ready;
  const bounds = svg.getBoundingClientRect();
  if (bounds.width <= 0 || bounds.height <= 0)
    throw new Error("차트가 표시된 뒤 다시 내보내세요.");

  const clone = svg.cloneNode(true) as SVGSVGElement;
  const originals = [svg, ...Array.from(svg.querySelectorAll("*"))];
  const copies = [clone, ...Array.from(clone.querySelectorAll("*"))];
  originals.forEach((original, index) => {
    const computed = getComputedStyle(original);
    const copy = copies[index] as SVGElement;
    SVG_STYLES.forEach((property) => {
      const value = computed.getPropertyValue(property);
      if (value) copy.style.setProperty(property, value);
    });
  });
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", String(bounds.width));
  clone.setAttribute("height", String(bounds.height));
  clone.style.width = `${bounds.width}px`;
  clone.style.height = `${bounds.height}px`;

  const styles = getComputedStyle(svg);
  const foreground = styles.color || "#162b2b";
  const background = backgroundOf(svg);
  const padding = 24;
  const width = Math.max(360, Math.ceil(bounds.width) + padding * 2);
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context) throw new Error("이 브라우저에서 PNG를 만들 수 없습니다.");
  const font = "system-ui, -apple-system, BlinkMacSystemFont, sans-serif";
  context.font = `600 19px ${font}`;
  const titleLines = wrapText(context, metadata.title, width - padding * 2);
  context.font = `13px ${font}`;
  const metadataLines = [
    metadata.period,
    `${metadata.unit ? `단위 ${metadata.unit}` : "단위 없음"}${metadata.aggregation ? ` · ${metadata.aggregation}` : ""}`,
    fetchedLabel(metadata.fetchedAt),
  ].flatMap((line) => wrapText(context, line, width - padding * 2));
  const legends = (metadata.legends ?? []).flatMap((legend) => {
    const label = typeof legend === "string" ? legend : legend.label;
    const color = typeof legend === "string" ? foreground : legend.color;
    return wrapText(context, label, width - padding * 2 - 22).map(
      (line, index) => ({
        label: line,
        color,
        first: index === 0,
      }),
    );
  });
  const headerHeight =
    padding + titleLines.length * 26 + metadataLines.length * 19 + 14;
  const height = Math.ceil(
    headerHeight + bounds.height + legends.length * 20 + padding,
  );
  if (width * 2 > 8192 || height * 2 > 8192)
    throw new Error("차트 크기를 줄여 다시 내보내세요.");
  canvas.width = width * 2;
  canvas.height = height * 2;
  context.scale(2, 2);
  context.fillStyle = background;
  context.fillRect(0, 0, width, height);
  context.fillStyle = foreground;
  context.font = `600 19px ${font}`;
  titleLines.forEach((line, index) =>
    context.fillText(line, padding, padding + 19 + index * 26),
  );
  context.font = `13px ${font}`;
  const metadataTop = padding + titleLines.length * 26;
  metadataLines.forEach((line, index) =>
    context.fillText(line, padding, metadataTop + 15 + index * 19),
  );

  const image = new Image();
  const source = URL.createObjectURL(
    new Blob([new XMLSerializer().serializeToString(clone)], {
      type: "image/svg+xml;charset=utf-8",
    }),
  );
  try {
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () =>
        reject(new Error("차트 이미지 변환에 실패했습니다. 다시 시도하세요."));
      image.src = source;
    });
    context.drawImage(
      image,
      padding,
      headerHeight,
      bounds.width,
      bounds.height,
    );
    context.font = `12px ${font}`;
    legends.forEach((legend, index) => {
      const y = headerHeight + bounds.height + 17 + index * 20;
      if (legend.first) {
        context.fillStyle = legend.color;
        context.fillRect(padding, y - 8, 10, 3);
      }
      context.fillStyle = foreground;
      context.fillText(legend.label, padding + 18, y);
    });
    const png = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (blob) =>
          blob ? resolve(blob) : reject(new Error("PNG 저장에 실패했습니다.")),
        "image/png",
      );
    });
    const download = URL.createObjectURL(png);
    const link = document.createElement("a");
    link.href = download;
    link.download =
      `${metadata.title}-${metadata.period}`
        .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "-")
        .slice(0, 160) + ".png";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(download), 1_000);
  } finally {
    URL.revokeObjectURL(source);
  }
}
