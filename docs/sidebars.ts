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
        'guides/unattended-install',
        'guides/gpu-passthrough',
        'guides/nvidia-nullmoth',
        'guides/intel-igpu-passthrough',
        'guides/faq',
      ],
    },
    {
      type: 'category',
      label: 'GPU BIOS settings',
      items: [
        'guides/gpu/bios/asus',
        'guides/gpu/bios/msi',
        'guides/gpu/bios/gigabyte',
        'guides/gpu/bios/asrock',
        'guides/gpu/bios/asrock-rack',
        'guides/gpu/bios/supermicro',
        'guides/gpu/bios/dell',
        'guides/gpu/bios/hp',
        'guides/gpu/bios/hpe',
        'guides/gpu/bios/lenovo',
        'guides/gpu/bios/intel-nuc',
        'guides/gpu/bios/mini-pc',
        'guides/gpu/bios/generic',
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
