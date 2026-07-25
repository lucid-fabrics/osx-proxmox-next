import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'OSX Proxmox Next',
  tagline: 'Deploy macOS VMs on Proxmox VE - automated, repeatable, painless',
  favicon: 'img/favicon.ico',

  headTags: [
    {
      tagName: 'script',
      attributes: {type: 'application/ld+json'},
      innerHTML: JSON.stringify({
        '@context': 'https://schema.org',
        '@type': 'SoftwareApplication',
        name: 'OSX Proxmox Next',
        applicationCategory: 'DeveloperApplication',
        operatingSystem: 'Proxmox VE 9 (Linux)',
        description:
          'Open-source tool that creates macOS virtual machines (Ventura, Sonoma, Sequoia, Tahoe) on Proxmox VE 9 with one command. TUI wizard, automatic OpenCore and recovery download, SMBIOS generation, Intel and AMD support.',
        url: 'https://lucid-fabrics.github.io/osx-proxmox-next/',
        offers: {'@type': 'Offer', price: '0', priceCurrency: 'USD'},
        sameAs: ['https://github.com/lucid-fabrics/osx-proxmox-next'],
        license: 'https://github.com/lucid-fabrics/osx-proxmox-next/blob/main/LICENSE',
      }),
    },
  ],

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
    image: 'img/macos-vnc-desktop.png',
    metadata: [
      {
        name: 'description',
        content:
          'Create macOS virtual machines on Proxmox VE 9 with one command. Supports Ventura, Sonoma, Sequoia, and Tahoe on Intel and AMD hosts. TUI wizard, automatic OpenCore and recovery download, SMBIOS generation.',
      },
      {
        name: 'keywords',
        content:
          'macos vm proxmox, macos on proxmox, proxmox macos vm, hackintosh proxmox, opencore proxmox, macos sequoia vm, macos tahoe vm, macos virtual machine, kvm macos, qemu macos',
      },
      {property: 'og:type', content: 'website'},
    ],
    announcementBar: {
      id: 'latest_release',
      content: 'New releases ship regularly: CPU auto-detection, Tahoe support, guest performance profiles. <a href="https://github.com/lucid-fabrics/osx-proxmox-next/releases/latest">See the latest release notes</a>',
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
              href: 'https://discord.gg/Ub6TunHYre',
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
