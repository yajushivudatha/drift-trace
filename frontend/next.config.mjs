/** @type {import('next').NextConfig} */
export default {
  output: "standalone",
  reactStrictMode: true,
  webpack: (config) => {
    // pdf.js has an optional Node-only dependency that the browser build never uses.
    config.resolve.alias.canvas = false;
    return config;
  },
};
