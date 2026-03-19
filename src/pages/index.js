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
      if (data.url) window.location.href = data.url;
    } catch (err) {
      alert('Error connecting to payment gateway.');
      setLoading(null);
    }
  };

  return (
    <div style={{ fontFamily: 'sans-serif', padding: '50px', textAlign: 'center' }}>
      <h1>Apex Enterprise OS</h1>
      <p>Deploy autonomous AI agent fleets.</p>
      
      <div style={{ display: 'flex', justifyContent: 'center', gap: '20px', marginTop: '40px' }}>
        <div style={{ border: '1px solid #ccc', padding: '30px', borderRadius: '10px' }}>
          <h2>Starter</h2>
          <h1>$299/mo</h1>
          <button onClick={() => handleCheckout('starter')} style={{ padding: '10px 20px', cursor: 'pointer' }}>
            {loading === 'starter' ? 'Loading...' : 'Checkout'}
          </button>
        </div>
        
        <div style={{ border: '2px solid blue', padding: '30px', borderRadius: '10px' }}>
          <h2>Professional</h2>
          <h1>$799/mo</h1>
          <button onClick={() => handleCheckout('professional')} style={{ padding: '10px 20px', background: 'blue', color: 'white', cursor: 'pointer' }}>
            {loading === 'professional' ? 'Loading...' : 'Checkout'}
          </button>
        </div>
      </div>
    </div>
  );
}
