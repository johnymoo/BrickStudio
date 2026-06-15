import { beforeEach, describe, expect, it } from "vitest";
import { useJobStore, selectJobList } from "@stores/useJobStore";

const SAMPLE_JOB = {
  id: "job-a",
  captureId: "cap-a",
  partId: "part-a",
  status: "pending" as const,
  progress: 0,
  stage: null,
  error: null,
  resultAssetId: null,
  createdAt: "2026-06-04T10:00:00.000Z",
  updatedAt: "2026-06-04T10:00:00.000Z",
  imageCount: 4,
};

describe("useJobStore", () => {
  beforeEach(() => {
    useJobStore.setState({ jobs: {}, currentJobId: null });
  });

  it("adds a job and sets it as current", () => {
    useJobStore.getState().addJob(SAMPLE_JOB);
    const state = useJobStore.getState();
    expect(state.jobs["job-a"]).toEqual(SAMPLE_JOB);
    expect(state.currentJobId).toBe("job-a");
  });

  it("updates an existing job", () => {
    useJobStore.getState().addJob(SAMPLE_JOB);
    useJobStore.getState().updateJob("job-a", { progress: 50, status: "running" });
    const updated = useJobStore.getState().jobs["job-a"]!;
    expect(updated.progress).toBe(50);
    expect(updated.status).toBe("running");
  });

  it("removes a job and clears currentJobId when applicable", () => {
    useJobStore.getState().addJob(SAMPLE_JOB);
    useJobStore.getState().removeJob("job-a");
    expect(useJobStore.getState().jobs["job-a"]).toBeUndefined();
    expect(useJobStore.getState().currentJobId).toBeNull();
  });

  it("selects job list sorted by createdAt desc", () => {
    useJobStore.getState().addJob(SAMPLE_JOB);
    useJobStore.getState().addJob({ ...SAMPLE_JOB, id: "job-b", createdAt: "2026-06-04T11:00:00.000Z" });
    const list = selectJobList(useJobStore.getState());
    expect(list[0]?.id).toBe("job-b");
    expect(list[1]?.id).toBe("job-a");
  });

  it("sets the capture mode on an existing job", () => {
    useJobStore.getState().addJob(SAMPLE_JOB);
    useJobStore.getState().setJobCaptureMode("job-a", "phone_walkaround");
    expect(useJobStore.getState().jobs["job-a"]?.captureMode).toBe("phone_walkaround");
  });

  it("setJobCaptureMode is a no-op for unknown ids", () => {
    useJobStore.getState().setJobCaptureMode("missing", "studio_turntable");
    expect(useJobStore.getState().jobs["missing"]).toBeUndefined();
  });
});
