import Stripe from 'stripe';

// Initialize with REAL production key injected via Vercel env
const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const { plan, data_tier } = req.body;
    
    // Map pricing to real dollars based on your portfolio architecture
    let priceAmount;
    if (plan === 'starter') priceAmount = 29900;     // $299.00
    if (plan === 'professional') priceAmount = 79900; // $799.00
    if (plan === 'enterprise') priceAmount = 199900;  // $1,999.00
    
    // Additional Data Tokenization / NWU volume logic
    if (data_tier === 'volume') priceAmount += 5000; // Add $50 for data stream access

    const session = await stripe.checkout.sessions.create({
      payment_method_types: ['card'],
      line_items: [
        {
          price_data: {
            currency: 'usd',
            product_data: {
              name: `Apex UEEP - ${plan.toUpperCase()} Plan`,
              description: 'Enterprise AI Operating System & Data Liquidity Access',
            },
            unit_amount: priceAmount,
          },
          quantity: 1,
        },
      ],
      mode: 'payment',
      // Route back to your Vercel deployment on success
      success_url: `https://apex-universal-ai-operating-system.vercel.app/dashboard?success=true`,
      cancel_url: `https://apex-universal-ai-operating-system.vercel.app/pricing?canceled=true`,
    });

    res.status(200).json({ sessionId: session.id, url: session.url });
  } catch (err) {
    console.error('Stripe error:', err.message);
    res.status(500).json({ error: 'Failed to create live checkout session' });
  }
}
