-- ==============================================================================
-- I2 insights — Supabase Cloud Database Schema v2
-- Run this in your Supabase Dashboard: SQL Editor -> New Query -> Run
-- ==============================================================================

-- 1. Doctors / Users Profile Table
CREATE TABLE IF NOT EXISTS public.doctors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    auth_id UUID UNIQUE,                          -- links to auth.users.id
    email TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    specialization TEXT DEFAULT 'General Physician',
    phone TEXT,
    avatar_url TEXT,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Screening Health Camps Table
CREATE TABLE IF NOT EXISTS public.camps (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    date_str TEXT DEFAULT 'Today',
    location TEXT,
    expected_records INTEGER DEFAULT 100,
    created_by UUID REFERENCES public.doctors(id),
    is_deleted BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Patient Intake Records Table (Strictly isolated by camp_id)
CREATE TABLE IF NOT EXISTS public.records (
    id TEXT PRIMARY KEY,
    camp_id TEXT NOT NULL REFERENCES public.camps(id) ON DELETE CASCADE,
    person_name TEXT NOT NULL,
    age TEXT,
    gender TEXT,
    dob TEXT,
    blood_group TEXT,
    mobile_number TEXT,
    address TEXT,
    height TEXT,
    weight TEXT,
    blood_pressure TEXT,
    pulse TEXT,
    rbs TEXT,
    waist_circumference TEXT,
    known_allergies TEXT,
    examination_details TEXT,
    referral_details TEXT,
    missing_fields JSONB DEFAULT '[]'::jsonb,
    uncertain_fields JSONB DEFAULT '[]'::jsonb,
    extraction_notes TEXT,
    structured_record JSONB,
    status TEXT DEFAULT 'needs_review',             -- 'needs_review' | 'reviewed' | 'failed'
    page1_preview TEXT,
    page2_preview TEXT,
    scanned_by UUID REFERENCES public.doctors(id),  -- doctor who scanned
    reviewer_notes TEXT,
    reviewed_by TEXT,                                -- doctor name who reviewed
    reviewed_by_id UUID REFERENCES public.doctors(id),
    reviewed_at TIMESTAMPTZ,
    is_deleted BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. High Performance Indexes
CREATE INDEX IF NOT EXISTS idx_records_camp_id ON public.records(camp_id);
CREATE INDEX IF NOT EXISTS idx_records_status ON public.records(status);
CREATE INDEX IF NOT EXISTS idx_records_created_at ON public.records(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_records_scanned_by ON public.records(scanned_by);
CREATE INDEX IF NOT EXISTS idx_records_person_name ON public.records(person_name);
CREATE INDEX IF NOT EXISTS idx_records_is_deleted ON public.records(is_deleted);
CREATE INDEX IF NOT EXISTS idx_camps_is_deleted ON public.camps(is_deleted);
CREATE INDEX IF NOT EXISTS idx_camps_created_by ON public.camps(created_by);
CREATE INDEX IF NOT EXISTS idx_doctors_email ON public.doctors(email);
CREATE INDEX IF NOT EXISTS idx_doctors_auth_id ON public.doctors(auth_id);

-- 5. Enable Row Level Security (RLS)
ALTER TABLE public.doctors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.camps ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.records ENABLE ROW LEVEL SECURITY;

-- 6. Access Policies (Open for field staff with anon key — tighten for production)
CREATE POLICY "Allow public read doctors" ON public.doctors FOR SELECT USING (true);
CREATE POLICY "Allow public insert doctors" ON public.doctors FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update doctors" ON public.doctors FOR UPDATE USING (true);

CREATE POLICY "Allow public read camps" ON public.camps FOR SELECT USING (true);
CREATE POLICY "Allow public insert camps" ON public.camps FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update camps" ON public.camps FOR UPDATE USING (true);
CREATE POLICY "Allow public delete camps" ON public.camps FOR DELETE USING (true);

CREATE POLICY "Allow public read records" ON public.records FOR SELECT USING (true);
CREATE POLICY "Allow public insert records" ON public.records FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update records" ON public.records FOR UPDATE USING (true);
CREATE POLICY "Allow public delete records" ON public.records FOR DELETE USING (true);

-- 7. Supabase Storage Bucket for Scanned Page Images
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'intake-scans',
    'intake-scans',
    true,
    10485760,  -- 10 MB
    ARRAY['image/jpeg', 'image/png', 'image/webp']
)
ON CONFLICT (id) DO NOTHING;

-- Storage policies for intake-scans bucket
CREATE POLICY "Allow public upload intake-scans" ON storage.objects
    FOR INSERT WITH CHECK (bucket_id = 'intake-scans');
CREATE POLICY "Allow public read intake-scans" ON storage.objects
    FOR SELECT USING (bucket_id = 'intake-scans');
CREATE POLICY "Allow public delete intake-scans" ON storage.objects
    FOR DELETE USING (bucket_id = 'intake-scans');

-- 8. Enable Realtime Publications for Multi-Doctor Live Sync
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.camps, public.records, public.doctors;
  END IF;
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- 9. Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_doctors_updated_at BEFORE UPDATE ON public.doctors
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE TRIGGER update_camps_updated_at BEFORE UPDATE ON public.camps
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE TRIGGER update_records_updated_at BEFORE UPDATE ON public.records
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
