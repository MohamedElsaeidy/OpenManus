import type { DependencyList } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { create } from 'zustand';
import { isEqual } from 'lodash';

type CacheStore = {
  caches: Map<boolean | string | symbol, unknown>;
  getCache: <R>(cacheKey: boolean | string | symbol) => R | undefined;
  setCache: <R>(cacheKey: boolean | string | symbol, data: R) => void;
};

const useCacheStore = create<CacheStore>((set, get) => ({
  caches: new Map(),
  getCache: <R>(cacheKey: boolean | string | symbol) => {
    const { caches } = get();
    return caches.get(cacheKey) as R | undefined;
  },
  setCache: (cacheKey, data) => {
    set(state => {
      const newCaches = new Map(state.caches);
      newCaches.set(cacheKey, data);
      return { caches: newCaches };
    });
  },
}));

export const useAsync = <R, T extends unknown[]>(
  fn: (...params: T) => Promise<R>,
  params: T,
  options: { manual?: boolean; deps?: DependencyList; skip?: (params: T) => boolean; cache?: string | symbol } = {},
) => {
  const [data, setData] = useState<R | undefined>(undefined);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | undefined>(undefined);
  const fnRef = useRef(fn);
  const paramsRef = useRef(params);
  const depsRef = useRef<DependencyList>(options.deps ?? []);
  const { getCache, setCache } = useCacheStore();

  const cacheKey = options.cache || false;
  if (!isEqual(depsRef.current, options.deps ?? [])) {
    depsRef.current = options.deps ?? [];
  }
  const stableDeps = depsRef.current;

  // Update function reference without triggering re-renders
  useEffect(() => {
    fnRef.current = fn;
  }, [fn]);

  // Deep compare params to prevent unnecessary re-renders
  useEffect(() => {
    if (!isEqual(paramsRef.current, params)) {
      paramsRef.current = params;
    }
  }, [params]);

  const shouldSkip = options.skip ? options.skip(params) : false;
  const run = useCallback(
    async (...runParams: T) => {
      if (stableDeps !== depsRef.current || shouldSkip) {
        return;
      }

      setIsLoading(true);
      try {
        const result = await fnRef.current(...runParams);
        setData(result);

        if (cacheKey) {
          setCache<R>(cacheKey, result);
        }

        return result;
      } catch (err) {
        setError(err as Error);
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [shouldSkip, cacheKey, setCache, stableDeps],
  );

  const refresh = useCallback(() => {
    return run(...paramsRef.current);
  }, [run]);

  const mutate = useCallback(
    (dataAction: R | undefined | ((prev: R | undefined) => R)) => {
      const newData = typeof dataAction === 'function' ? (dataAction as (prev: R | undefined) => R)(data) : dataAction;
      setData(newData);
      setError(undefined);

      if (cacheKey && newData !== undefined) {
        setCache<R>(cacheKey, newData);
      }

      return newData;
    },
    [data, cacheKey, setCache],
  );

  useEffect(() => {
    if (options.manual) {
      return;
    }

    if (cacheKey) {
      const cachedData = getCache<R>(cacheKey);
      if (cachedData !== undefined) {
        setData(cachedData);
        return;
      }
    }
    run(...paramsRef.current);
  }, [run, options.manual, cacheKey, getCache]);

  return { data, isLoading, error, run, refresh, mutate };
};
