import React, { useState } from 'react';

export default function LandingPage() {
  const [loading, setLoading] = useState(null);

  const handleCheckout = async (plan) => {
    setLoading(plan);
    try {
      const res = await fetch('/api/checkout/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan, data_tier: 'standard' }),
      });
      const data = await res.json();
      if (data.url) {
        window.location.href = data.url; // Redirects user to Stripe checkout
      } else {
        alert('Checkout failed: ' + (data.error || 'Unknown error'));
        setLoading(null);
      }
    } catch (err) {
      alert('Error connecting to payment gateway.');
      setLoading(null);
    }
  };

  return (
    <div style={{ fontFamily: 'system-ui, sans-serif', padding: '60px 20px', maxWidth: '1200px', margin: '0 auto', textAlign: 'center' }}>
      <h1 style={{ fontSize: '3.5rem', fontWeight: '900', color: '#111', letterSpacing: '-1px' }}>
        Apex Enterprise OS
      </h1>
      <p style={{ fontSize: '1.25rem', color: '#555', maxWidth: '600px', margin: '0 auto 50px' }}>
        Deploy autonomous AI agent fleets, tokenize your data, and scale your revenue operations instantly.
      </p>
      
      <div style={{ display: 'flex', justifyContent: 'center', gap: '30px', flexWrap: 'wrap' }}>
        
        {/* Starter Tier */}
        <div style={{ border: '1px solid #eaeaea', borderRadius: '12px', padding: '40px 30px', width: '320px', boxShadow: '0 4px 6px rgba(0,0,0,0.05)', display: 'flex', flexDirection: 'column' }}>
          <h3 style={{ fontSize: '1.5rem', margin: '0 0 10px' }}>Starter</h3>
          <div style={{ fontSize: '3rem', fontWeight: 'bold', margin: '10px 0' }}>$299<span style={{ fontSize: '1rem', color: '#888' }}>/mo</span></div>
          <ul style={{ textAlign: 'left', margin: '20px 0 40px', paddingLeft: '20px', color: '#444', flexGrow: 1 }}>
            <li style={{ marginBottom: '10px' }}>Basic AI Automation</li>
            <li style={{ marginBottom: '10px' }}>Single Agent Pipeline</li>
            <li style={{ marginBottom: '10px' }}>Community Support</li>
          </ul>
          <button 
            disabled={loading === 'starter'} 
            onClick={() => handleCheckout('starter')} 
            style={{ width: '100%', padding: '15px', backgroundColor: '#111', color: 'white', border: 'none', borderRadius: '8px', cursor: 'pointer', fontSize: '1.1rem', fontWeight: 'bold' }}>
            {loading === 'starter' ? 'Processing...' : 'Deploy Now'}
          </button>
        </div>
        
        {/* Professional Tier (Highlighted) */}
        <div style={{ border: '2px solid #0070f3', borderRadius: '12px', padding: '40px 30px', width: '320px', boxShadow: '0 12px 24px rgba(0,112,243,0.15)', display: 'flex', flexDirection: 'column', position: 'relative' }}>
          <div style={{ position: 'absolute', top: '-12px', left: '50%', transform: 'translateX(-50%)', backgroundColor: '#0070f3', color: 'white', padding: '4px 12px', borderRadius: '20px', fontSize: '0.85rem', fontWeight: 'bold' }}>MOST POPULAR</div>
          <h3 style={{ fontSize: '1.5rem', margin: '0 0 10px', color: '#0070f3' }}>Professional</h3>
          <div style={{ fontSize: '3rem', fontWeight: 'bold', margin: '10px 0' }}>$799<span style={{ fontSize: '1rem', color: '#888' }}>/mo</span></div>
          <ul style={{ textAlign: 'left', margin: '20px 0 40px', paddingLeft: '20px', color: '#444', flexGrow: 1 }}>
            <li style={{ marginBottom: '10px' }}>Advanced Orchestration</li>
            <li style={{ marginBottom: '10px' }}>5 AI Agent Grid</li>
            <li style={{ marginBottom: '10px' }}>Priority 24/7 Support</li>
          </ul>
          <button 
            disabled={loading === 'professional'} 
            onClick={() => handleCheckout('professional')} 
            style={{ width: '100%', padding: '15px', backgroundColor: '#0070f3', color: 'white', border: 'none', borderRadius: '8px', cursor: 'pointer', fontSize: '1.1rem', fontWeight: 'bold' }}>
            {loading === 'professional' ? 'Processing...' : 'Deploy Now'}
          </button>
        </div>

        {/* Enterprise Tier */}
        <div style={{ border: '1px solid #eaeaea', borderRadius: '12px', padding: '40px 30px', width: '320px', boxShadow: '0 4px 6px rgba(0,0,0,0.05)', display: 'flex', flexDirection: 'column' }}>
          <h3 style={{ fontSize: '1.5rem', margin: '0 0 10px' }}>Enterprise</h3>
          <div style={{ fontSize: '3rem', fontWeight: 'bold', margin: '10px 0' }}>$1,999<span style={{ fontSize: '1rem', color: '#888' }}>/mo</span></div>
          <ul style={{ textAlign: 'left', margin: '20px 0 40px', paddingLeft: '20px', color: '#444', flexGrow: 1 }}>
            <li style={{ marginBottom: '10px' }}>NWU Data Tokenization</li>
            <li style={{ marginBottom: '10px' }}>Unlimited AI Agents</li>
            <li style={{ marginBottom: '10px' }}>Custom Cloud Infrastructure</li>
          </ul>
          <button 
            disabled={loading === 'enterprise'} 
            onClick={() => handleCheckout('enterprise')} 
            style={{ width: '100%', padding: '15px', backgroundColor: '#111', color: 'white', border: 'none', borderRadius: '8px', cursor: 'pointer', fontSize: '1.1rem', fontWeight: 'bold' }}>
            {loading === 'enterprise' ? 'Processing...' : 'Get Access'}
          </button>
        </div>

      </div>
    </div>
  );
}
