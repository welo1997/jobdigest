/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export — the landing + lifecycle pages are all client-rendered and talk to the
  // FastAPI backend (service/webapp.py) over the network. Deploys as static files behind
  // Cloudflare (Phase C). API base comes from NEXT_PUBLIC_API_URL.
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
