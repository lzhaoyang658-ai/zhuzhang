export type RecentLoginHandler = () => Promise<void>;

export function createRecentLoginCoordinator() {
  let handler: RecentLoginHandler | null = null;
  let activeAttempt: Promise<void> | null = null;
  let completedGeneration = 0;

  function registerRecentLoginHandler(nextHandler: RecentLoginHandler) {
    handler = nextHandler;
    return () => {
      if (handler === nextHandler) handler = null;
    };
  }

  function recentLoginGeneration() {
    return completedGeneration;
  }

  function ensureRecentLogin(unavailableError: Error): Promise<void> {
    if (activeAttempt) return activeAttempt;
    if (!handler) return Promise.reject(unavailableError);

    const attempt = Promise.resolve()
      .then(() => handler?.() ?? Promise.reject(unavailableError))
      .then(() => {
        completedGeneration += 1;
      })
      .finally(() => {
        if (activeAttempt === attempt) activeAttempt = null;
      });

    activeAttempt = attempt;
    return attempt;
  }

  return { registerRecentLoginHandler, recentLoginGeneration, ensureRecentLogin };
}

const recentLoginCoordinator = createRecentLoginCoordinator();

export const registerRecentLoginHandler = recentLoginCoordinator.registerRecentLoginHandler;
export const recentLoginGeneration = recentLoginCoordinator.recentLoginGeneration;
export const ensureRecentLogin = recentLoginCoordinator.ensureRecentLogin;
