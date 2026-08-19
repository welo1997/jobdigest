import { usePathname } from "next/navigation";

/**
 * Top-bar active-page helper, shared by the two halves of the nav (`SiteChrome`'s public link
 * and `AuthNav`'s account links). Lives here rather than in either component so the two do not
 * import each other — `SiteChrome` already imports `AuthNav`, and a back-import would be a cycle
 * between two client components.
 *
 * Returns a matcher that yields the `" active"` class suffix for the page currently shown. An
 * exact match: every nav link is a leaf page (`/en/jobs`, `/en/matches`, `/en/preferences`), and
 * the locale is already baked into both sides by `href()` and `usePathname`, so no prefix logic
 * is needed.
 */
export function useNavActive(): (target: string) => string {
  const pathname = usePathname();
  return (target: string) => (pathname === target ? " active" : "");
}
