/**
 * Shared SWR fetcher that throws on non-ok responses.
 *
 * Without this, fetch() resolves on 4xx/5xx (only rejects on network errors).
 * SWR's errorRetryCount can't cap retries if the fetcher never throws.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const fetcher = async (url: string): Promise<any> => {
  const res = await fetch(url);

  if (!res.ok) {
    const error = new Error(`Fetch error ${res.status}`);
    (error as any).status = res.status;
    throw error;
  }

  return res.json();
};
