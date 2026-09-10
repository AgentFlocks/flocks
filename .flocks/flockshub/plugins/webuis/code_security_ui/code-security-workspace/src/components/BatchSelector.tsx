import { useEffect, useState } from "react";
import { cancelBatchTask, getBatch, listBatches, readApiFailure } from "../api";
import { useCodeSecurityI18n } from "../i18n";

export function BatchSelector({
  batchId,
  taskId,
}: {
  batchId: string;
  taskId: string;
}) {
  const { t } = useCodeSecurityI18n();
  const [batches, setBatches] = useState<any[]>([]);
  const [tasks, setTasks] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const labels: Record<string, string> = {
    pending: "待执行",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
    interrupted: "已中断",
    cancelled: "已取消",
    timed_out: "已超时",
  };
  useEffect(() => setCancelling(false), [batchId, taskId]);
  useEffect(() => {
    let disposed = false;
    listBatches()
      .then((items) => {
        if (!disposed) setBatches(items || []);
      })
      .catch((reason) => {
        if (!disposed)
          setError(readApiFailure(reason, t("无法加载批次")).message);
      });
    return () => {
      disposed = true;
    };
  }, [t]);
  useEffect(() => {
    if (!batchId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const batch = await getBatch(batchId);
        if (!disposed) {
          setTasks(batch.tasks || []);
          setError("");
        }
      } catch (reason) {
        if (!disposed)
          setError(readApiFailure(reason, t("无法加载批次")).message);
      } finally {
        if (!disposed) timer = setTimeout(refresh, 3000);
      }
    };
    void refresh();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [batchId, t]);
  function navigate(batch: string, task = "") {
    const params = new URLSearchParams();
    if (batch) params.set("batch_id", batch);
    if (task) params.set("task_id", task);
    window.history.pushState({}, "", `${window.location.pathname}?${params}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }
  const selected = tasks.find((item) => item.task_id === taskId);
  return (
    <section className="cs-batch-selector" aria-label={t("批量审计")}>
      <label>
        {t("批量审计")}
        <select
          value={batchId}
          onChange={(event) => navigate(event.target.value)}
        >
          <option value="">{t("普通审计")}</option>
          {batches.map((batch) => (
            <option key={batch.batch_id} value={batch.batch_id}>
              {batch.created_at} · {batch.task_count} {t("个任务")}
            </option>
          ))}
        </select>
      </label>
      {batchId && (
        <label>
          {t("任务")}
          <select
            value={taskId}
            onChange={(event) => navigate(batchId, event.target.value)}
          >
            <option value="">{t("选择任务")}</option>
            {tasks.map((task) => (
              <option key={task.task_id} value={task.task_id}>
                {task.task_id} · {t(labels[task.status] || task.status)}
              </option>
            ))}
          </select>
        </label>
      )}
      {selected && (
        <span role="status">
          {t("任务")} {taskId} · {t(labels[selected.status] || selected.status)}
          {selected.error || selected.cleanup_error
            ? ` · ${selected.error || selected.cleanup_error}`
            : ""}
        </span>
      )}
      {selected && ["pending", "running"].includes(selected.status) && (
        <button
          type="button"
          className="cs-button cs-button--secondary"
          disabled={cancelling}
          onClick={async () => {
            setCancelling(true);
            try {
              await cancelBatchTask(batchId, taskId);
            } catch (reason) {
              setError(readApiFailure(reason, t("取消失败")).message);
              setCancelling(false);
            }
          }}
        >
          {t(cancelling ? "正在取消" : "取消任务")}
        </button>
      )}
      {error && <span role="alert">{error}</span>}
    </section>
  );
}
