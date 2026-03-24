import type {ReactNode} from 'react';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';
import Heading from '@theme/Heading';

export default function NotFound(): ReactNode {
  return (
    <Layout title="Page Not Found">
      <main style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '60vh',
        textAlign: 'center',
        padding: '2rem',
      }}>
        <Heading as="h1" style={{fontSize: '4rem', marginBottom: '0.5rem'}}>
          404
        </Heading>
        <p style={{fontSize: '1.3rem', marginBottom: '2rem', opacity: 0.7}}>
          This page doesn't exist. Maybe it was a bad boot order.
        </p>
        <div style={{display: 'flex', gap: '1rem', flexWrap: 'wrap', justifyContent: 'center'}}>
          <Link className="button button--primary button--lg" to="/docs/intro">
            Back to Docs
          </Link>
          <Link className="button button--secondary button--lg" to="/docs/guides/troubleshooting">
            Troubleshooting
          </Link>
        </div>
      </main>
    </Layout>
  );
}
