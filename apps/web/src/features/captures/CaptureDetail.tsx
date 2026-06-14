import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getCapture, getCaptureImages, type CaptureImageInfo, type CaptureInfo } from "@lib/api";

export function CaptureDetail() {
  const { id } = useParams();
  const [capture, setCapture] = useState<CaptureInfo | null>(null);
  const [images, setImages] = useState<CaptureImageInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    setError(null);
    Promise.all([
      getCapture(id, controller.signal),
      getCaptureImages(id, controller.signal).catch(() => ({ capture_id: id, images: [] })),
    ])
      .then(([captureInfo, imageInfo]) => {
        setCapture(captureInfo);
        setImages(imageInfo.images);
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "加载采集失败");
      });
    return () => controller.abort();
  }, [id]);

  if (!id) return null;

  if (error) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-6">
        <div className="card text-sm text-err">{error}</div>
      </div>
    );
  }

  if (!capture) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-6">
        <div className="card text-sm text-txt-secondary">加载采集中...</div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-txt-primary">{capture.part_id}</h1>
          <p className="mt-1 text-xs text-txt-secondary">
            {capture.mode ?? "photo"} · {capture.image_count} 张照片
          </p>
        </div>
        {capture.job_id ? (
          <Link to={`/jobs/${capture.job_id}`} className="btn-secondary text-sm">
            查看任务
          </Link>
        ) : null}
      </div>

      {capture.needs_measurement ? (
        <section className="card mb-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 className="text-base font-medium text-txt-primary">需要补充测量</h2>
              <p className="mt-1 text-sm text-txt-secondary">{capture.needs_measurement.guidance}</p>
              {capture.needs_measurement.reason ? (
                <p className="mt-2 text-xs text-txt-tertiary">原因：{capture.needs_measurement.reason}</p>
              ) : null}
            </div>
            <Link to="/parametric" className="btn-primary shrink-0 text-sm">
              开始测量
            </Link>
          </div>
        </section>
      ) : null}

      <section>
        <h2 className="mb-2 text-base font-medium text-txt-primary">采集照片</h2>
        {images.length > 0 ? (
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {images.map((image) => (
              <li key={image.key} className="overflow-hidden rounded-lg border border-line bg-surface">
                <a href={image.url} target="_blank" rel="noreferrer" className="block">
                  <img src={image.url} alt={image.key} className="aspect-square w-full object-cover" />
                </a>
                <div className="truncate px-2 py-1 text-[10px] text-txt-tertiary">{image.key}</div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="card text-sm text-txt-secondary">
            {capture.image_keys.length > 0 ? capture.image_keys.join(", ") : "没有可显示的照片"}
          </div>
        )}
      </section>
    </div>
  );
}
