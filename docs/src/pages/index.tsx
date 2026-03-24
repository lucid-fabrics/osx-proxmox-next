import {useState, type ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import useBaseUrl from '@docusaurus/useBaseUrl';

import styles from './index.module.css';

const features = [
  {
    icon: '🔍',
    title: 'Auto-Detection',
    description: 'Detects your CPU (Intel, AMD, Xeon), cores, RAM, and storage pools. Picks the right boot flags automatically.',
  },
  {
    icon: '🔧',
    title: 'OpenCore Built-In',
    description: 'Builds a GPT+ESP bootloader with all required kexts and drivers. No manual OpenCore setup.',
  },
  {
    icon: '🆔',
    title: 'SMBIOS Generation',
    description: 'Generates valid Apple serials, MLB, UUID, and ROM. iCloud, iMessage, and FaceTime just work.',
  },
  {
    icon: '📦',
    title: 'Smart Caching',
    description: 'Downloads recovery and OpenCore once, reuses across VMs. Use shared storage for multi-node clusters.',
  },
  {
    icon: '👁️',
    title: 'Dry-Run Preview',
    description: 'Review every qm command before it touches your system. Nothing runs until you approve.',
  },
  {
    icon: '⚡',
    title: 'Two Interfaces',
    description: 'Interactive TUI wizard for first-timers. CLI with JSON output for scripting and automation.',
  },
];

const versions = [
  { name: 'Ventura', version: 'macOS 13', badge: 'Full Apple Services', badgeClass: styles.badgeGreen },
  { name: 'Sonoma', version: 'macOS 14', badge: 'Recommended', badgeClass: styles.badgeGreen, recommended: true },
  { name: 'Sequoia', version: 'macOS 15', badge: 'Limited Services', badgeClass: styles.badgeYellow },
  { name: 'Tahoe', version: 'macOS 26', badge: 'Bleeding Edge', badgeClass: styles.badgeOrange },
];

const INSTALL_CMD = 'bash -c "$(curl -fsSL https://raw.githubusercontent.com/lucid-fabrics/osx-proxmox-next/main/install.sh)"';

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(INSTALL_CMD);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <header className={styles.heroBanner}>
      <div className="container">
        <Heading as="h1" className={styles.heroTitle}>
          {siteConfig.title}
        </Heading>
        <p className={styles.heroSubtitle}>{siteConfig.tagline}</p>
        <div className={styles.buttons}>
          <Link className="button button--primary button--lg" to="/docs/intro">
            Get Started
          </Link>
          <Link
            className="button button--secondary button--lg"
            href="https://github.com/lucid-fabrics/osx-proxmox-next">
            GitHub
          </Link>
        </div>
        <div className={styles.installBlock} onClick={handleCopy} title="Click to copy">
          <code>{INSTALL_CMD}</code>
          <span className={styles.copyIcon}>{copied ? '✓' : '📋'}</span>
          {copied && <span className={styles.copyToast}>Copied!</span>}
        </div>
      </div>
    </header>
  );
}

function BeforeAfter() {
  return (
    <section className={styles.compareSection}>
      <div className="container">
        <Heading as="h2" className={styles.sectionTitle}>
          The Manual Way vs. One Command
        </Heading>
        <p className={styles.sectionSubtitle}>
          What used to take a weekend now takes minutes
        </p>
        <div className={styles.compareGrid}>
          <div className={clsx(styles.compareCard, styles.manual)}>
            <div className={clsx(styles.compareLabel, styles.compareLabelRed)}>Without this tool</div>
            <ul className={styles.compareList}>
              <li>Download OpenCore manually</li>
              <li>Find the right kexts for your version</li>
              <li>Edit config.plist by hand</li>
              <li>Build GPT disk image with dd</li>
              <li>Generate SMBIOS with GenSMBIOS</li>
              <li>Figure out QEMU args for your CPU</li>
              <li>Download recovery from Apple API</li>
              <li>Convert DMG with dmg2img</li>
              <li>Write 15+ qm commands</li>
              <li>Debug boot failures alone</li>
            </ul>
          </div>
          <div className={styles.compareVs}>vs</div>
          <div className={clsx(styles.compareCard, styles.automated)}>
            <div className={clsx(styles.compareLabel, styles.compareLabelGreen)}>With OSX Proxmox Next</div>
            <ul className={styles.compareList}>
              <li>Run one command</li>
              <li>Pick your macOS version</li>
              <li>Pick your storage</li>
              <li>Review the dry-run</li>
              <li>Hit install</li>
              <li>&nbsp;</li>
              <li>&nbsp;</li>
              <li>&nbsp;</li>
              <li>&nbsp;</li>
              <li>Done.</li>
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}

function HowItWorks() {
  return (
    <section className={styles.howSection}>
      <div className="container">
        <Heading as="h2" className={styles.sectionTitle}>
          How It Works
        </Heading>
        <p className={styles.sectionSubtitle}>
          Three steps from bare metal to macOS desktop
        </p>
        <div className={styles.stepsGrid}>
          <div className={styles.stepCard}>
            <div className={styles.stepNumber}>1</div>
            <div className={styles.stepTitle}>Install</div>
            <div className={styles.stepDesc}>
              One command installs the tool on your Proxmox node. Python venv is created automatically.
            </div>
            <img
              src={useBaseUrl('/img/screenshots/step1-preflight.svg')}
              alt="Preflight checks"
              className={styles.stepScreenshot}
            />
          </div>
          <div className={styles.stepCard}>
            <div className={styles.stepNumber}>2</div>
            <div className={styles.stepTitle}>Choose</div>
            <div className={styles.stepDesc}>
              Pick your macOS version and storage. CPU, RAM, and SMBIOS are auto-configured.
            </div>
            <img
              src={useBaseUrl('/img/screenshots/step2-choose-os.svg')}
              alt="Choose macOS version"
              className={styles.stepScreenshot}
            />
          </div>
          <div className={styles.stepCard}>
            <div className={styles.stepNumber}>3</div>
            <div className={styles.stepTitle}>Run</div>
            <div className={styles.stepDesc}>
              Review the dry-run, hit install. OpenCore, recovery, and VM are created automatically.
            </div>
            <img
              src={useBaseUrl('/img/screenshots/step5-review.svg')}
              alt="Review and install"
              className={styles.stepScreenshot}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function Features() {
  return (
    <section className={styles.features}>
      <div className="container">
        <Heading as="h2" className={styles.sectionTitle}>
          Everything You Need
        </Heading>
        <p className={styles.sectionSubtitle}>
          Built for the Proxmox community
        </p>
        <div className={styles.featureGrid}>
          {features.map((f, i) => (
            <div key={i} className={styles.featureCard}>
              <div className={styles.featureIcon}>{f.icon}</div>
              <div className={styles.featureTitle}>{f.title}</div>
              <div className={styles.featureDesc}>{f.description}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Stats() {
  return (
    <section className={styles.statsBar}>
      <div className={styles.statsGrid}>
        <div className={styles.statItem}>
          <div className={styles.statValue}>4</div>
          <div className={styles.statLabel}>macOS Versions</div>
        </div>
        <div className={styles.statItem}>
          <div className={styles.statValue}>3</div>
          <div className={styles.statLabel}>CPU Platforms</div>
        </div>
        <div className={styles.statItem}>
          <div className={styles.statValue}>6</div>
          <div className={styles.statLabel}>Wizard Steps</div>
        </div>
        <div className={styles.statItem}>
          <div className={styles.statValue}>&lt;10min</div>
          <div className={styles.statLabel}>Setup Time</div>
        </div>
      </div>
    </section>
  );
}

function Versions() {
  return (
    <section className={styles.versionsSection}>
      <div className="container">
        <Heading as="h2" className={styles.sectionTitle}>
          Supported macOS Versions
        </Heading>
        <p className={styles.sectionSubtitle}>
          From stable and tested to bleeding edge
        </p>
        <div className={styles.versionGrid}>
          {versions.map((v, i) => (
            <div key={i} className={clsx(styles.versionCard, v.recommended && styles.recommended)}>
              <div className={styles.versionName}>{v.name}</div>
              <div className={styles.versionNumber}>{v.version}</div>
              <span className={clsx(styles.versionBadge, v.badgeClass)}>{v.badge}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}


function Community() {
  const quotes = [
    {
      text: 'osx-next worked. And it\'s created the VM, started it and the VM is showing Tahoe!',
      author: '@rndthoughts',
      context: 'First successful Tahoe install',
      link: 'https://github.com/lucid-fabrics/osx-proxmox-next/issues/17',
    },
    {
      text: 'With the fix, I got past the issue and Sonoma is now installing. Installation worked!',
      author: '@bjornik',
      context: 'Sonoma install on Proxmox 9',
      link: 'https://github.com/lucid-fabrics/osx-proxmox-next/issues/49',
    },
    {
      text: 'Awesome, thanks for the quick response and resolution!',
      author: '@superdooper86',
      context: 'SMBIOS fix for Proxmox 9 base64 encoding',
      link: 'https://github.com/lucid-fabrics/osx-proxmox-next/issues/5',
    },
  ];

  return (
    <section className={styles.testimonialSection}>
      <div className="container">
        <Heading as="h2" className={styles.sectionTitle}>
          Built with the Community
        </Heading>
        <p className={styles.sectionSubtitle}>
          Real feedback from real users shaping this tool
        </p>
        <div className={styles.quoteGrid}>
          {quotes.map((q, i) => (
            <a key={i} href={q.link} className={styles.quoteCard} target="_blank" rel="noopener noreferrer">
              <div className={styles.quoteText}>"{q.text}"</div>
              <div className={styles.quoteAuthor}>{q.author}</div>
              <div className={styles.quoteContext}>{q.context}</div>
            </a>
          ))}
        </div>
      </div>
    </section>
  );
}

function Architecture() {
  return (
    <section className={styles.archSection}>
      <div className="container">
        <Heading as="h2" className={styles.sectionTitle}>
          Under the Hood
        </Heading>
        <p className={styles.sectionSubtitle}>
          Plan-then-execute architecture with mandatory dry-run
        </p>
        <div className={styles.archDiagram}>
          <div className={styles.archFlow}>
            <span className={clsx(styles.archStep, styles.archStepHighlight)}>Your Config</span>
            <span className={styles.archArrow}>→</span>
            <span className={clsx(styles.archStep, styles.archStepNormal)}>Validate</span>
            <span className={styles.archArrow}>→</span>
            <span className={clsx(styles.archStep, styles.archStepNormal)}>Plan</span>
            <span className={styles.archArrow}>→</span>
            <span className={clsx(styles.archStep, styles.archStepNormal)}>Dry-Run</span>
            <span className={styles.archArrow}>→</span>
            <span className={clsx(styles.archStep, styles.archStepHighlight)}>Execute</span>
            <span className={styles.archArrow}>→</span>
            <span className={clsx(styles.archStep, styles.archStepHighlight)}>macOS VM</span>
          </div>
        </div>
        <p style={{marginTop: '1.5rem'}}>
          <Link to="/docs/architecture/overview">Explore the full architecture →</Link>
        </p>
      </div>
    </section>
  );
}

function CTA() {
  return (
    <section className={styles.ctaSection}>
      <div className="container">
        <Heading as="h2" className={styles.ctaTitle}>
          Ready to run macOS on Proxmox?
        </Heading>
        <p className={styles.ctaSubtitle}>
          One command. Pick your macOS. Hit install. That's it.
        </p>
        <div className={styles.buttons}>
          <Link className="button button--primary button--lg" to="/docs/getting-started/quick-start">
            Quick Start Guide
          </Link>
          <Link
            className="button button--secondary button--lg"
            href="https://discord.gg/2M5RJSGd">
            Join Discord
          </Link>
        </div>
      </div>
    </section>
  );
}

export default function Home(): ReactNode {
  return (
    <Layout
      title="macOS VMs on Proxmox"
      description="Deploy macOS VMs on Proxmox VE — automated, repeatable, painless">
      <HomepageHeader />
      <main>
        <BeforeAfter />
        <HowItWorks />
        <Features />
        <Stats />
        <Versions />
        <Community />
        <Architecture />
        <CTA />
      </main>
    </Layout>
  );
}
