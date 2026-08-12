/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:7801/api/:path*',
      },
    ];
  },
};

module.exports = nextConfig;
