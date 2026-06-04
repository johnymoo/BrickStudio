import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { JobStatus } from "@lib/api";

export interface JobRecord {
  id: string; // job id
  captureId: string;
  partId: string;
  status: JobStatus;
  progress: number;
  stage?: string | null;
  error?: string | null;
  resultAssetId?: string | null;
  createdAt: string; // ISO
  updatedAt: string; // ISO
  imageCount: number;
}

export interface JobState {
  jobs: Record<string, JobRecord>;
  currentJobId: string | null;
  addJob: (job: JobRecord) => void;
  updateJob: (id: string, patch: Partial<JobRecord>) => void;
  setCurrent: (id: string | null) => void;
  removeJob: (id: string) => void;
  clearAll: () => void;
}

const STORAGE_KEY = "blocktool.jobs.v1";

/**
 * Persisted Zustand store. Hydrates from localStorage on first read.
 *
 * The persistence key (`blocktool.jobs.v1`) is versioned so a future schema
 * change can be migrated without polluting the user's previous data.
 */
export const useJobStore = create<JobState>()(
  persist(
    (set) => ({
      jobs: {},
      currentJobId: null,
      addJob: (job) =>
        set((state) => ({
          jobs: { ...state.jobs, [job.id]: job },
          currentJobId: job.id,
        })),
      updateJob: (id, patch) =>
        set((state) => {
          const existing = state.jobs[id];
          if (!existing) return state;
          return {
            jobs: {
              ...state.jobs,
              [id]: { ...existing, ...patch, updatedAt: new Date().toISOString() },
            },
          };
        }),
      setCurrent: (id) => set({ currentJobId: id }),
      removeJob: (id) =>
        set((state) => {
          const { [id]: _omit, ...rest } = state.jobs;
          return {
            jobs: rest,
            currentJobId: state.currentJobId === id ? null : state.currentJobId,
          };
        }),
      clearAll: () => set({ jobs: {}, currentJobId: null }),
    }),
    {
      name: STORAGE_KEY,
      storage: createJSONStorage(() => localStorage),
      version: 1,
      partialize: (state) => ({ jobs: state.jobs, currentJobId: state.currentJobId }),
    },
  ),
);

/** Selector helper: returns the list of jobs sorted by createdAt desc. */
export function selectJobList(state: JobState): JobRecord[] {
  return Object.values(state.jobs).sort((a, b) =>
    a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0,
  );
}
