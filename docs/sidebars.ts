import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docsSidebar: [
    'intro',
    {
      type: 'category',
      label: 'Getting Started',
      items: [
        'getting-started/requirements',
        'getting-started/installation',
        'getting-started/quick-start',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      items: [
        'guides/tui-wizard',
        'guides/cli-reference',
        'guides/apple-services',
        'guides/shared-storage',
        'guides/troubleshooting',
        'guides/macos-versions',
        'guides/faq',
      ],
    },
    {
      type: 'category',
      label: 'Architecture',
      items: [
        'architecture/overview',
        'architecture/opencore',
        'architecture/smbios',
      ],
    },
  ],
};

export default sidebars;
