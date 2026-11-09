"use client";

import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

/** Renders a PDF with a selectable text layer, and reports what the reader highlights. */
export default function PdfViewer({ url, onSelectText, height = "60vh" }: { url: string; onSelectText?: (text: string) => void; height?: string }) {
  const [pages, setPages] = useState(0);
  const [width, setWidth] = useState(600);
  const [error, setError] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width - 24)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);

  const capture = () => {
    const text = window.getSelection()?.toString().replace(/\s+/g, " ").trim();
    if (text && text.length > 8) onSelectText?.(text);
  };

  return (
    <div ref={ref} onMouseUp={capture} style={{ height }} className="overflow-y-auto rounded-[5px] border border-line bg-sunken p-3">
      {error ? (
        <p className="text-ink-2">This PDF could not be displayed. Its text can still be read in the Text tab.</p>
      ) : (
        <Document file={url} onLoadSuccess={({ numPages }) => setPages(numPages)} onLoadError={() => setError(true)} loading={<p className="text-ink-2">Loading PDF</p>}>
          {Array.from({ length: pages }, (_, index) => (
            <Page key={index} pageNumber={index + 1} width={width} className="mb-3" renderAnnotationLayer={false} />
          ))}
        </Document>
      )}
    </div>
  );
}
