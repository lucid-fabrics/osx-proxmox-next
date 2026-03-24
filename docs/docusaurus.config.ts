import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'OSX Proxmox Next',
  tagline: 'Deploy macOS VMs on Proxmox VE — automated, repeatable, painless',
  favicon: 'img/favicon.ico',

  future: {
    v4: true,
  },

  url: 'https://lucid-fabrics.github.io',
  baseUrl: '/osx-proxmox-next/',

  organizationName: 'lucid-fabrics',
  projectName: 'osx-proxmox-next',

  onBrokenLinks: 'throw',

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  markdown: {
    mermaid: true,
  },

  themes: [
    [
      '@easyops-cn/docusaurus-search-local',
      {
        hashed: true,
        docsRouteBasePath: '/docs',
        indexBlog: false,
      },
    ],
    '@docusaurus/theme-mermaid',
  ],

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl:
            'https://github.com/lucid-fabrics/osx-proxmox-next/tree/main/docs/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    announcementBar: {
      id: 'v0_16',
      content: 'v0.16.0 is out — Xeon CPU detection, e1000 NIC support, and architectural improvements. <a href="https://github.com/lucid-fabrics/osx-proxmox-next/releases/latest">See release notes</a>',
      backgroundColor: '#e57000',
      textColor: '#fff',
      isCloseable: true,
    },
    colorMode: {
      defaultMode: 'dark',
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'OSX Proxmox Next',
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docsSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          type: 'html',
          position: 'right',
          value: '<a href="https://github.com/lucid-fabrics/osx-proxmox-next/releases" target="_blank" rel="noopener noreferrer"><img src="https://img.shields.io/github/v/release/lucid-fabrics/osx-proxmox-next?style=flat-square&label=version" alt="version" /></a>',
        },
        {
          href: 'https://github.com/lucid-fabrics/osx-proxmox-next',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Documentation',
          items: [
            {
              label: 'Getting Started',
              to: '/docs/getting-started/quick-start',
            },
            {
              label: 'CLI Reference',
              to: '/docs/guides/cli-reference',
            },
            {
              label: 'FAQ',
              to: '/docs/guides/faq',
            },
          ],
        },
        {
          title: 'Community',
          items: [
            {
              label: 'GitHub Issues',
              href: 'https://github.com/lucid-fabrics/osx-proxmox-next/issues',
            },
            {
              label: 'Discord',
              href: 'https://discord.gg/2M5RJSGd',
            },
          ],
        },
        {
          title: 'Support',
          items: [
            {
              label: 'Ko-fi',
              href: 'https://ko-fi.com/lucidfabrics',
            },
            {
              label: 'Buy Me a Coffee',
              href: 'https://buymeacoffee.com/lucidfabrics',
            },
            {
              label: 'GitHub Sponsors',
              href: 'https://github.com/sponsors/lucid-fabrics',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Lucid Fabrics. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'python', 'toml'],
      magicComments: [],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
