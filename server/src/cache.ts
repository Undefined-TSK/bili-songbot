/** 进程内 TTL + LRU 缓存（搜索结果 / 播放 URL 复用）。 */
export class TTLCache<V> {
  private data = new Map<string, { ts: number; value: V }>();
  hits = 0;
  misses = 0;

  constructor(
    private ttlSeconds: number,
    private maxSize = 256,
  ) {}

  get enabled(): boolean {
    return this.ttlSeconds > 0;
  }

  get(key: string): V | undefined {
    const item = this.data.get(key);
    if (!item) {
      this.misses += 1;
      return undefined;
    }
    if ((Date.now() - item.ts) / 1000 >= this.ttlSeconds) {
      this.data.delete(key);
      this.misses += 1;
      return undefined;
    }
    this.data.delete(key);
    this.data.set(key, item);
    this.hits += 1;
    return item.value;
  }

  set(key: string, value: V): void {
    if (!this.enabled) return;
    this.data.delete(key);
    this.data.set(key, { ts: Date.now(), value });
    while (this.data.size > this.maxSize) {
      const first = this.data.keys().next().value as string | undefined;
      if (first === undefined) break;
      this.data.delete(first);
    }
  }

  invalidate(key: string): void {
    this.data.delete(key);
  }

  clear(): void {
    this.data.clear();
  }

  get size(): number {
    return this.data.size;
  }
}
