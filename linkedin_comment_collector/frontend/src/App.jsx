import React, { useState, useEffect, useMemo, useRef } from 'react';
import {
  Sparkles,
  Link as LinkIcon,
  Download,
  ChevronDown,
  FileDown,
  Users,
  Mail,
  Phone,
  MessageSquare,
  ExternalLink,
  Copy,
  Check,
  History,
  X,
  Layers,
  LayoutGrid,
  List,
  CheckCircle2,
  Sun,
  Moon,
  Upload,
  FileSpreadsheet
} from 'lucide-react';

// Robust helper to extract and clean LinkedIn post URLs from text
const extractLinkedInUrls = (text) => {
  if (!text) return [];
  const pattern = /(?:https?:\/\/)?(?:www\.)?(?:linkedin\.com\/(?:posts|feed\/update|pulse)\/[^\s'"<>,;]+|lnkd.in\/[^\s'"<>,;]+)/gi;
  const matches = text.match(pattern) || [];
  const cleanList = [];
  const seen = new Set();
  for (let m of matches) {
    let clean = m.replace(/[.,;)>'"]+$/, '');
    if (!clean.startsWith('http://') && !clean.startsWith('https://')) {
      clean = 'https://' + clean;
    }
    if (clean && !seen.has(clean)) {
      seen.add(clean);
      cleanList.push(clean);
    }
  }
  return cleanList;
};

// Extracts the official company website / domain
const GENERIC_EMAIL_DOMAINS = [
  'gmail.com', 'googlemail.com', 'yahoo.com', 'yahoo.in', 'yahoo.co.in', 'ymail.com', 'rediffmail.com',
  'hotmail.com', 'outlook.com', 'live.com', 'msn.com', 'icloud.com', 'me.com', 'mac.com', 'aol.com',
  'proton.me', 'protonmail.com', 'zoho.com', 'zohomail.in', 'mail.com', 'gmx.com', 'gmx.net', 'yandex.com', 'fastmail.com'
];

// Older backend versions stored links guessed from personal email usernames (venky@gmail.com -> venky.in,
// linkedin.com/company/venky). Detects such stored values so they are never shown as real company data.
const isGuessedFromPersonalEmail = (value, email) => {
  if (!value || !email || !email.includes('@')) return false;
  const [user, domain] = email.toLowerCase().trim().split('@');
  if (!user || !GENERIC_EMAIL_DOMAINS.includes(domain)) return false;
  const v = value.toLowerCase().replace(/^https?:\/\//, '').replace(/^www\./, '').replace(/\/+$/, '');
  const userSlug = user.replace(/[^a-z0-9]/g, '-');
  const host = v.split('/')[0];
  return host.split('.')[0] === user || v === `linkedin.com/company/${user}` || v === `linkedin.com/company/${userSlug}`;
};

const getCompanyWebsite = (companyWebsite, rawCompanyUrl, email, commentText) => {
  const genericDomains = GENERIC_EMAIL_DOMAINS;
  if (isGuessedFromPersonalEmail(companyWebsite, email)) companyWebsite = null;
  if (isGuessedFromPersonalEmail(rawCompanyUrl, email)) rawCompanyUrl = null;

  // 1. Direct verified website stored in company_website
  if (companyWebsite && typeof companyWebsite === 'string' && companyWebsite.startsWith('http')) {
    const domain = companyWebsite.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0];
    return {
      url: companyWebsite,
      display: domain
    };
  }

  // 2. Fallback: If legacy rawCompanyUrl contains a website link (not a linkedin page)
  if (rawCompanyUrl && typeof rawCompanyUrl === 'string') {
    if (rawCompanyUrl.startsWith('http://') || rawCompanyUrl.startsWith('https://')) {
      if (!rawCompanyUrl.includes('linkedin.com/company/')) {
        const domain = rawCompanyUrl.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0];
        return {
          url: rawCompanyUrl,
          display: domain
        };
      }
    }
  }

  // 3. Derive directly from corporate email domain
  if (email && email.includes('@')) {
    const domain = email.split('@')[1].toLowerCase().trim();
    if (domain && !genericDomains.includes(domain)) {
      return {
        url: `https://${domain}`,
        display: domain
      };
    }
  }

  // 4. Check comment text for explicit website link
  if (commentText) {
    const match = commentText.match(/(?:https?:\/\/)?(?:www\.)?([a-zA-Z0-9-]+\.(?:com|org|net|io|ai|co|in|us|uk|ca|de|fr|tech|biz|info|global))(?:[^\s,;)<>"]*)?/i);
    if (match && match[1]) {
      const dom = match[1].toLowerCase().trim();
      if (!genericDomains.includes(dom) && !dom.includes('linkedin.com') && !dom.includes('lnkd.in')) {
        return {
          url: `https://${dom}`,
          display: dom
        };
      }
    }
  }

  // Personal emails (gmail, yahoo, ...) carry no company domain, so no website is guessed from the username
  return null;
};

// Extracts and validates the official company LinkedIn URL (always derived if email/website is present)
const getCompanyLinkedIn = (companyLinkedInUrl, email, website) => {
  if (isGuessedFromPersonalEmail(companyLinkedInUrl, email)) companyLinkedInUrl = null;
  if (companyLinkedInUrl && typeof companyLinkedInUrl === 'string' && companyLinkedInUrl.includes('linkedin.com/company/')) {
    const parts = companyLinkedInUrl.split('/company/');
    const slug = parts[1] ? parts[1].split('/')[0].split('?')[0] : 'Company';
    return {
      url: companyLinkedInUrl,
      display: slug
    };
  }

  // Guaranteed fallback: If email is present, derive company LinkedIn URL from corporate domain
  const genericDomains = GENERIC_EMAIL_DOMAINS;
  if (email && email.includes('@')) {
    const domain = email.split('@')[1];
    const d = domain ? domain.toLowerCase().trim() : '';
    if (d && !genericDomains.includes(d)) {
      const companySlug = d.split('.')[0];
      if (companySlug && companySlug.length >= 2) {
        return {
          url: `https://www.linkedin.com/company/${companySlug}`,
          display: companySlug
        };
      }
    }
  }

  // Guaranteed fallback: If website is present, derive company LinkedIn URL from website domain
  if (website && typeof website === 'string' && website.startsWith('http')) {
    const cleanDomain = website.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0];
    const companySlug = cleanDomain.split('.')[0];
    if (companySlug && companySlug.length >= 2 && !genericDomains.includes(cleanDomain)) {
      return {
        url: `https://www.linkedin.com/company/${companySlug}`,
        display: companySlug
      };
    }
  }

  return null;
};





export default function App() {
  const [theme, setTheme] = useState(() => {
    return localStorage.getItem('leadstream-theme') || 'dark';
  });
  
  // Link Ingestion State: Single vs Multi/Batch & Uploads
  const [inputMode, setInputMode] = useState('single'); // 'single' | 'batch'
  const [postUrl, setPostUrl] = useState('');
  const [batchText, setBatchText] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [uploadedFileName, setUploadedFileName] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [batchProgress, setBatchProgress] = useState(null); // { total, current, currentUrl, completed: [] }
  const fileInputRef = useRef(null);

  const [maxComments, setMaxComments] = useState(100); // 100 safe default to conserve Apify credits
  const scraperProvider = 'apify';
  const apifyToken = localStorage.getItem('leadstream-apify-token') || '';
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractStep, setExtractStep] = useState('');
  
  // Active Data State
  const [currentPost, setCurrentPost] = useState(null);
  const [comments, setComments] = useState([]);
  
  // Filter States
  const [searchQuery, setSearchQuery] = useState('');
  const [deduplicateAuthors, setDeduplicateAuthors] = useState(true);
  const [excludeAuthorReplies, setExcludeAuthorReplies] = useState(false);
  const [contactOnly, setContactOnly] = useState(false);
  const [activeView, setActiveView] = useState('table'); // 'table' | 'cards'
  
  // History Drawer & Toast State
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [isExportDropdownOpen, setIsExportDropdownOpen] = useState(false);
  const [historyPosts, setHistoryPosts] = useState([]);
  const [toastMessage, setToastMessage] = useState(null);
  const [backendStatus, setBackendStatus] = useState('checking'); // 'healthy' | 'offline'

  // Copy helper feedback
  const [copiedId, setCopiedId] = useState(null);
  const [selectedLead, setSelectedLead] = useState(null); // Interactive Lead Detail Drawer

  const copyAllEmails = () => {
    const emails = filteredComments
      .map(c => c.email)
      .filter(Boolean);
    const uniqueEmails = Array.from(new Set(emails));
    if (uniqueEmails.length === 0) {
      showToast('No emails found in currently filtered leads');
      return;
    }
    navigator.clipboard.writeText(uniqueEmails.join(', '));
    showToast(`Copied ${uniqueEmails.length} unique emails to clipboard!`);
    setCopiedId('bulk-emails');
    setTimeout(() => setCopiedId(null), 2500);
  };

  const showToast = (msg) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3000);
  };

  const copyToClipboard = (text, id) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    showToast(`Copied "${text}" to clipboard`);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const resumePollingJob = async (jobId) => {
    const MAX_POLL_ATTEMPTS = 180; // 6 minutes max (180 * 2s)
    try {
      setIsExtracting(true);
      setExtractStep('Checking previous extraction job status...');
      let isDone = false;
      let attempts = 0;
      while (!isDone && attempts < MAX_POLL_ATTEMPTS) {
        attempts += 1;
        const jobRes = await fetch(`/api/jobs/${jobId}`);
        if (!jobRes.ok) {
          sessionStorage.removeItem('leadstream-active-job');
          break;
        }
        const jobData = await jobRes.json();
        if (jobData.message) setExtractStep(jobData.message);
        if (jobData.status === 'COMPLETED') {
          isDone = true;
          sessionStorage.removeItem('leadstream-active-job');
          const resData = jobData.result;
          if (resData && resData.post) {
            setCurrentPost(resData.post);
            setComments(resData.comments || []);
            fetchHistory();
            showToast(`Success! Extracted ${resData.total_comments} comments.`);
          }
        } else if (jobData.status === 'FAILED') {
          isDone = true;
          sessionStorage.removeItem('leadstream-active-job');
          showToast(`Error: ${jobData.error || 'Previous extraction job failed or was interrupted'}`);
        } else {
          await new Promise(r => setTimeout(r, 2000));
        }
      }
      if (!isDone) {
        sessionStorage.removeItem('leadstream-active-job');
        showToast('Previous extraction job timed out after 6 minutes. Please start a new scrape.');
      }
    } catch {
      sessionStorage.removeItem('leadstream-active-job');
    } finally {
      setIsExtracting(false);
      setExtractStep('');
    }
  };

  const buildExportUrl = (postId, format = 'csv') => {
    const params = new URLSearchParams();
    params.set('format', format);
    if (deduplicateAuthors) params.set('deduplicate_authors', 'true');
    if (contactOnly) params.set('contact_only', 'true');
    if (excludeAuthorReplies) params.set('exclude_replies', 'true');
    if (searchQuery.trim()) params.set('search', searchQuery.trim());
    return `/api/export/${postId}?${params.toString()}`;
  };

  // Sync data-theme attribute on root element
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('leadstream-theme', theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme(prev => (prev === 'dark' ? 'light' : 'dark'));
  };

  const checkHealth = async () => {
    try {
      const res = await fetch('/api/health');
      if (res.ok) {
        setBackendStatus('healthy');
      } else {
        setBackendStatus('offline');
      }
    } catch {
      setBackendStatus('offline');
    }
  };

  const fetchHistory = async () => {
    try {
      const res = await fetch('/api/posts');
      const data = await res.json();
      if (data && data.posts) {
        setHistoryPosts(data.posts);
        // Keep view fresh on startup: history remains hidden until explicitly opened
      }
    } catch (err) {
      console.error('Error fetching history:', err);
    }
  };

  // Check backend health, fetch recent history, and resume any in-progress job on mount
  useEffect(() => {
    checkHealth();
    fetchHistory();
    const savedJobId = sessionStorage.getItem('leadstream-active-job');
    if (savedJobId) {
      resumePollingJob(savedJobId);
    }
  }, []);

  const loadCombinedView = async () => {
    try {
      const res = await fetch('/api/posts/combined');
      const data = await res.json();
      if (data && data.comments) {
        setCurrentPost({
          id: 'combined',
          title: `All Scraped Posts Combined (${data.total_posts || historyPosts.length} Posts)`,
          canonical_url: '',
          original_url: '',
          total_comments_count: data.total_comments,
          isCombined: true,
          posts: data.posts || historyPosts
        });
        setComments(data.comments || []);
        setIsHistoryOpen(false);
        showToast(`Loaded Combined View: ${data.total_comments} leads across ${data.total_posts || historyPosts.length} posts`);
      }
    } catch {
      showToast('Failed to load combined view');
    }
  };

  const loadPost = async (postId) => {
    if (postId === 'combined') {
      return loadCombinedView();
    }
    try {
      const res = await fetch(`/api/posts/${postId}`);
      const data = await res.json();
      if (data && data.post) {
        setCurrentPost(data.post);
        setComments(data.comments || []);
        setIsHistoryOpen(false);
        showToast(`Loaded "${data.post.title || 'Post'}" (${data.total_comments} comments)`);
      }
    } catch {
      showToast('Failed to load post data');
    }
  };

  // Detected batch URLs derived from batchText
  const detectedBatchUrls = useMemo(() => {
    return extractLinkedInUrls(batchText);
  }, [batchText]);

  // If user pastes multiple URLs into single link input, auto-switch to batch mode
  const handleSinglePaste = (e) => {
    const text = e.clipboardData.getData('text');
    const detected = extractLinkedInUrls(text);
    if (detected.length > 1) {
      e.preventDefault();
      setInputMode('batch');
      setBatchText(text);
      showToast(`Detected ${detected.length} LinkedIn links! Switched to Multi-Link mode.`);
    }
  };

  // Process uploaded document (CSV, Excel, Word, PDF, TXT)
  const processUploadedFile = async (file) => {
    const validExtensions = ['.csv', '.txt', '.pdf', '.docx', '.doc', '.xlsx', '.xlsm', '.xls'];
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!validExtensions.includes(ext)) {
      showToast(`Unsupported format (${ext}). Please upload CSV, Excel (.xlsx/.xls), Word (.docx), PDF, or TXT.`);
      return;
    }

    setIsUploading(true);
    showToast(`Parsing ${file.name} for LinkedIn links...`);
    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await fetch('/api/parse-links', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to parse file');
      }

      if (data.urls && data.urls.length > 0) {
        setInputMode('batch');
        setUploadedFileName(data.filename);
        setBatchText(prev => {
          const existing = extractLinkedInUrls(prev);
          const combined = Array.from(new Set([...existing, ...data.urls]));
          return combined.join('\n');
        });
        showToast(`Extracted ${data.found_count} LinkedIn post links from ${data.filename}!`);
      } else {
        showToast(`No LinkedIn post URLs were found in ${data.filename}. Check that links start with linkedin.com/ or lnkd.in/`);
      }
    } catch (err) {
      showToast(`Upload error: ${err.message}`);
    } finally {
      setIsUploading(false);
    }
  };

  const handleFileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    await processUploadedFile(file);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  // Drag and Drop support on the hero box
  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragging(true);
  };
  const handleDragLeave = (e) => {
    e.preventDefault();
    setIsDragging(false);
  };
  const handleDrop = async (e) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      await processUploadedFile(e.dataTransfer.files[0]);
    }
  };

  const removeBatchUrl = (urlToRemove) => {
    const updated = detectedBatchUrls.filter(u => u !== urlToRemove);
    setBatchText(updated.join('\n'));
  };

  const handleExtract = async (e) => {
    e.preventDefault();

    if (inputMode === 'single') {
      const cleanUrl = postUrl.trim();
      const urlsInSingle = extractLinkedInUrls(cleanUrl);
      if (urlsInSingle.length > 1) {
        setInputMode('batch');
        setBatchText(cleanUrl);
        showToast(`Detected ${urlsInSingle.length} links! Switched to batch mode.`);
        return;
      }

      if (!cleanUrl) {
        showToast('Please enter a valid LinkedIn post URL');
        return;
      }

      setIsExtracting(true);
      setBatchProgress(null);
      setExtractStep('Connecting to Apify Actor Cloud Engine...');

      const stepTimer1 = setTimeout(() => {
        setExtractStep('Scraping 500+ comments, headlines & reactions via Apify...');
      }, 3000);

      const stepTimer2 = setTimeout(() => {
        setExtractStep('Enriching company websites, LinkedIn profiles & persisting to SQLite...');
      }, 8000);

      try {
        // Enqueue background extraction task to prevent 504 timeouts on cloud proxies
        const res = await fetch('/api/extract/async', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            post_url: cleanUrl,
            max_comments: parseInt(maxComments, 10),
            provider: scraperProvider,
            apify_token: apifyToken.trim() || undefined
          })
        });

        clearTimeout(stepTimer1);
        clearTimeout(stepTimer2);

        const data = await res.json();
        if (!res.ok) {
          const detailMsg = typeof data.detail === 'string' ? data.detail : 'Failed to start extraction task';
          throw new Error(detailMsg);
        }

        const jobId = data.job_id;
        sessionStorage.setItem('leadstream-active-job', jobId);
        setExtractStep('Scraping comments and identifying commenters...');

        // Poll job status until completion with a 6-minute safety bound
        let isDone = false;
        let pollAttempts = 0;
        const MAX_POLL_ATTEMPTS = 180;
        while (!isDone && pollAttempts < MAX_POLL_ATTEMPTS) {
          pollAttempts += 1;
          await new Promise(r => setTimeout(r, 2000));
          const jobRes = await fetch(`/api/jobs/${jobId}`);
          if (!jobRes.ok) continue;
          const jobData = await jobRes.json();
          if (jobData.message) setExtractStep(jobData.message);

          if (jobData.status === 'COMPLETED') {
            isDone = true;
            sessionStorage.removeItem('leadstream-active-job');
            const resData = jobData.result;
            setCurrentPost(resData.post);
            setComments(resData.comments || []);
            fetchHistory();
            showToast(`Success! Extracted ${resData.total_comments} comments.`);
          } else if (jobData.status === 'FAILED') {
            isDone = true;
            sessionStorage.removeItem('leadstream-active-job');
            throw new Error(jobData.error || 'Extraction job failed');
          }
        }
        if (!isDone) {
          sessionStorage.removeItem('leadstream-active-job');
          throw new Error('Extraction timed out after 6 minutes. Please retry with a smaller limit.');
        }
      } catch (err) {
        clearTimeout(stepTimer1);
        clearTimeout(stepTimer2);
        showToast(`Error: ${typeof err === 'object' && err.message ? err.message : String(err)}`);
      } finally {
        setIsExtracting(false);
        setExtractStep('');
      }
    } else {
      // Batch mode
      const targetUrls = detectedBatchUrls;
      if (targetUrls.length === 0) {
        showToast('Please enter at least one LinkedIn post URL or upload a file');
        return;
      }

      setIsExtracting(true);
      setBatchProgress({
        total: targetUrls.length,
        current: 1,
        currentUrl: targetUrls[0],
        completed: []
      });

      const completed = [];
      let latestPost = null;
      let latestComments = [];

      for (let i = 0; i < targetUrls.length; i++) {
        const url = targetUrls[i];
        setBatchProgress({
          total: targetUrls.length,
          current: i + 1,
          currentUrl: url,
          completed: [...completed]
        });
        setExtractStep(`Scraping post ${i + 1} of ${targetUrls.length}...`);

        try {
          const res = await fetch('/api/extract/async', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              post_url: url,
              max_comments: parseInt(maxComments, 10),
              provider: scraperProvider,
              apify_token: apifyToken.trim() || undefined
            })
          });

          const data = await res.json();
          if (!res.ok) {
            completed.push({
              url,
              success: false,
              error: data.detail || 'Failed to enqueue job'
            });
            continue;
          }

          const jobId = data.job_id;
          let isJobDone = false;
          let batchPollAttempts = 0;
          const MAX_BATCH_POLLS = 180;
          while (!isJobDone && batchPollAttempts < MAX_BATCH_POLLS) {
            batchPollAttempts += 1;
            await new Promise(r => setTimeout(r, 2000));
            const jobRes = await fetch(`/api/jobs/${jobId}`);
            if (!jobRes.ok) continue;
            const jobData = await jobRes.json();
            if (jobData.status === 'COMPLETED') {
              isJobDone = true;
              const resData = jobData.result;
              completed.push({
                url,
                success: true,
                total_comments: resData.total_comments || 0,
                title: resData.post?.title || url
              });
              latestPost = resData.post;
              latestComments = resData.comments || [];
            } else if (jobData.status === 'FAILED') {
              isJobDone = true;
              completed.push({
                url,
                success: false,
                error: jobData.error || 'Scraping failed'
              });
            }
          }
          if (!isJobDone) {
            completed.push({
              url,
              success: false,
              error: 'Timed out after 6 minutes'
            });
          }
        } catch (err) {
          completed.push({
            url,
            success: false,
            error: err.message
          });
        }
      }

      setBatchProgress(prev => prev ? { ...prev, completed: [...completed] } : null);

      await fetchHistory();

      const successCount = completed.filter(c => c.success).length;
      if (successCount > 1) {
        await loadCombinedView();
      } else if (latestPost) {
        setCurrentPost(latestPost);
        setComments(latestComments);
      }
      showToast(`Batch completed: ${successCount} of ${targetUrls.length} posts scraped successfully.`);
      setIsExtracting(false);
      setExtractStep('');
    }
  };

  // Filtered leads
  const filteredComments = useMemo(() => {
    const raw = comments.filter(c => {
      // Exclude author replies if requested
      if (excludeAuthorReplies && c.depth > 0) {
        return false;
      }

      // Contact info only
      if (contactOnly && !c.email && !c.phone) {
        return false;
      }

      // Search keyword
      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase();
        const nameMatch = (c.author_name || '').toLowerCase().includes(query);
        const headlineMatch = (c.author_headline || '').toLowerCase().includes(query);
        const commentMatch = (c.comment_text || '').toLowerCase().includes(query);
        const emailMatch = (c.email || '').toLowerCase().includes(query);
        const phoneMatch = (c.phone || '').toLowerCase().includes(query);
        const websiteMatch = (c.company_website || '').toLowerCase().includes(query);
        const companyMatch = (c.company_linkedin_url || '').toLowerCase().includes(query);
        return nameMatch || headlineMatch || commentMatch || emailMatch || phoneMatch || websiteMatch || companyMatch;
      }

      return true;
    });

    if (!deduplicateAuthors) {
      return raw;
    }

    const dedupMap = new Map();
    for (const item of raw) {
      const key = (item.author_profile_url || '').trim().toLowerCase() || (item.author_name || '').trim().toLowerCase();
      if (!key) {
        dedupMap.set(item.id, { ...item, commentCount: 1, allComments: [item.comment_text] });
        continue;
      }
      if (!dedupMap.has(key)) {
        dedupMap.set(key, {
          ...item,
          commentCount: 1,
          allComments: [item.comment_text]
        });
      } else {
        const existing = dedupMap.get(key);
        existing.commentCount += 1;
        if (item.comment_text && !existing.allComments.includes(item.comment_text)) {
          existing.allComments.push(item.comment_text);
        }
        if (!existing.email && item.email) existing.email = item.email;
        if (!existing.phone && item.phone) existing.phone = item.phone;
        if (!existing.author_headline && item.author_headline) existing.author_headline = item.author_headline;
        if (!existing.company_website && item.company_website) existing.company_website = item.company_website;
        if (!existing.company_linkedin_url && item.company_linkedin_url) existing.company_linkedin_url = item.company_linkedin_url;
        if (!existing.post_title && item.post_title) existing.post_title = item.post_title;
      }
    }
    return Array.from(dedupMap.values());
  }, [comments, excludeAuthorReplies, contactOnly, searchQuery, deduplicateAuthors]);

  // KPI Metrics
  const stats = useMemo(() => {
    const total = comments.length;
    const uniqueProfiles = new Set(comments.map(c => c.author_profile_url || c.author_name)).size;
    const emailsCount = comments.filter(c => c.email && c.email.trim() !== '').length;
    const phonesCount = comments.filter(c => c.phone && c.phone.trim() !== '').length;

    return { total, uniqueProfiles, emailsCount, phonesCount };
  }, [comments]);

  const getInitials = (name) => {
    if (!name) return 'U';
    const parts = name.trim().split(' ');
    if (parts.length === 1) return parts[0].substring(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  };

  return (
    <div className="app-container">
      {/* Top Application Header */}
      <header className="navbar">
        <div className="brand-wrapper">
          <div className="brand-logo" aria-label="LeadStream logo">
            <Layers size={16} color="#ffffff" strokeWidth={2.2} />
          </div>
          <div className="brand-meta">
            <span className="brand-title">LeadStream</span>
          </div>
        </div>

        {/* Quick Post Switcher for Active / Historical Posts */}
        {historyPosts.length > 0 && (
          <div className="header-post-picker" title="Switch active dataset">
            <Layers size={13} className="picker-icon" />
            <select
              aria-label="Active Scraped Post Switcher"
              className="header-post-select"
              value={currentPost ? currentPost.id : ''}
              onChange={(e) => {
                if (e.target.value === 'combined') {
                  loadCombinedView();
                } else if (e.target.value) {
                  loadPost(e.target.value);
                }
              }}
            >
              {historyPosts.length > 1 && (
                <option value="combined">
                  ★ All Combined Posts ({historyPosts.reduce((acc, p) => acc + (p.total_comments_count || 0), 0)} leads)
                </option>
              )}
              <option value="" disabled>Individual Datasets...</option>
              {historyPosts.map(p => (
                <option key={p.id} value={p.id}>
                  {p.title ? (p.title.length > 45 ? p.title.substring(0, 45) + '...' : p.title) : 'Untitled dataset'} ({p.total_comments || p.total_comments_count || 0} leads)
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="nav-badges">
          <div 
            className={`status-pill ${backendStatus === 'healthy' ? 'status-online' : 'status-offline'}`}
          >
            <span className="status-dot"></span>
            <span className="status-text">
              {backendStatus === 'healthy' ? 'Connected' : 'Offline'}
            </span>
          </div>

          <button 
            type="button" 
            className="btn-secondary nav-history-btn"
            onClick={() => setIsHistoryOpen(true)}
            aria-label="Open Saved Datasets Drawer"
          >
            <History size={13} />
            <span className="history-btn-text">History</span>
            <span className="history-count-badge">{historyPosts.length}</span>
          </button>

          <button
            type="button"
            className="btn-secondary theme-toggle-btn"
            onClick={toggleTheme}
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          >
            {theme === 'dark' ? <Sun size={13} /> : <Moon size={13} />}
          </button>
        </div>
      </header>

      {/* Primary URL Ingestion Bar & Multi-Link / Upload Section */}
      <section 
        className={`hero-box ${isDragging ? 'hero-box-dragging' : ''}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {/* Ingest Control Header: Mode Switcher & File Upload */}
        <div className="ingest-controls-bar">
          <div className="mode-toggle-group">
            <button
              type="button"
              className={`mode-tab-btn ${inputMode === 'single' ? 'active' : ''}`}
              onClick={() => setInputMode('single')}
              disabled={isExtracting}
            >
              <LinkIcon size={12} />
              Single Link
            </button>
            <button
              type="button"
              className={`mode-tab-btn ${inputMode === 'batch' ? 'active' : ''}`}
              onClick={() => setInputMode('batch')}
              disabled={isExtracting}
            >
              <Layers size={12} />
              Multi-Link / Batch
              {detectedBatchUrls.length > 0 && (
                <span className="mode-badge">{detectedBatchUrls.length}</span>
              )}
            </button>
          </div>

          <div className="upload-action-group">
            {/* Scraping Limit Selector to control Apify consumption */}
            <div className="scrape-limit-selector" title="Comments limit per post">
              <span className="scrape-limit-label">LIMIT:</span>
              <select
                aria-label="Comments limit per post"
                className="scrape-limit-select"
                value={maxComments}
                onChange={(e) => setMaxComments(Number(e.target.value))}
                disabled={isExtracting}
              >
                <option value={50}>50 comments</option>
                <option value={100}>100 comments</option>
                <option value={250}>250 comments</option>
                <option value={500}>500 comments</option>
                <option value={1000}>1,000 comments</option>
              </select>
            </div>
            <input
              type="file"
              ref={fileInputRef}
              accept=".csv,.txt,.pdf,.docx,.doc,.xlsx,.xlsm,.xls"
              onChange={handleFileUpload}
              style={{ display: 'none' }}
              disabled={isExtracting || isUploading}
            />
            <button
              type="button"
              className="btn-upload-file"
              onClick={() => fileInputRef.current?.click()}
              disabled={isExtracting || isUploading}
              title="Upload CSV, Excel (.xlsx/.xls), Word (.docx), or PDF file to extract LinkedIn links"
            >
              {isUploading ? (
                <>
                  <div className="spinner-mini"></div>
                  Parsing File...
                </>
              ) : (
                <>
                  <Upload size={12} />
                  Upload Doc / CSV / Excel
                </>
              )}
            </button>
          </div>
        </div>

        {/* Ingest Form */}
        <form onSubmit={handleExtract}>
          {inputMode === 'single' ? (
            <div className="input-row">
              <div className="url-input-wrapper">
                <input
                  type="text"
                  className="url-input"
                  placeholder="Paste single LinkedIn post URL to scrape (or paste multiple to switch to batch)..."
                  value={postUrl}
                  onChange={(e) => setPostUrl(e.target.value)}
                  onPaste={handleSinglePaste}
                  disabled={isExtracting}
                />
              </div>

              <button
                type="submit"
                className="btn-primary"
                disabled={isExtracting || !postUrl.trim()}
              >
                {isExtracting ? (
                  <>
                    <div className="spinner"></div>
                    Scraping...
                  </>
                ) : (
                  'Fetch Comments'
                )}
              </button>
            </div>
          ) : (
            <div className="batch-input-container">
              <div className="batch-textarea-wrapper">
                <textarea
                  className="batch-textarea"
                  rows={3}
                  placeholder="Paste multiple LinkedIn post URLs (one per line, comma-separated, or paste document text)..."
                  value={batchText}
                  onChange={(e) => setBatchText(e.target.value)}
                  disabled={isExtracting}
                />
              </div>

              {/* Detected links metadata bar */}
              <div className="batch-meta-row">
                <div className="batch-meta-left">
                  <span className="badge-detected-count">
                    <Sparkles size={11} />
                    {detectedBatchUrls.length} {detectedBatchUrls.length === 1 ? 'post link' : 'post links'} detected
                  </span>
                  {uploadedFileName && (
                    <span className="badge-file-attached">
                      <FileSpreadsheet size={11} />
                      {uploadedFileName}
                      <button
                        type="button"
                        className="btn-remove-file"
                        onClick={() => setUploadedFileName('')}
                        title="Clear uploaded file"
                      >
                        <X size={10} />
                      </button>
                    </span>
                  )}
                  {detectedBatchUrls.length > 0 && (
                    <button
                      type="button"
                      className="btn-text-clear"
                      onClick={() => {
                        setBatchText('');
                        setUploadedFileName('');
                      }}
                    >
                      Clear all
                    </button>
                  )}
                </div>

                <div className="batch-meta-right">
                  <button
                    type="submit"
                    className="btn-primary"
                    disabled={isExtracting || detectedBatchUrls.length === 0}
                  >
                    {isExtracting ? (
                      <>
                        <div className="spinner"></div>
                        Scraping Batch...
                      </>
                    ) : (
                      `Fetch Comments (${detectedBatchUrls.length})`
                    )}
                  </button>
                </div>
              </div>

              {/* Collapsible Detected Links Tray */}
              {detectedBatchUrls.length > 0 && (
                <div className="detected-links-tray">
                  {detectedBatchUrls.map((url, idx) => (
                    <div key={idx} className="detected-link-chip" title={url}>
                      <span className="chip-index">{idx + 1}</span>
                      <span className="chip-url-text">{url}</span>
                      <button
                        type="button"
                        className="chip-remove-btn"
                        onClick={() => removeBatchUrl(url)}
                        title="Remove this URL"
                      >
                        <X size={11} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </form>
      </section>

      {/* Extraction in progress animation */}
      {isExtracting && (
        <div className="extracting-banner animate-fade">
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flex: 1, minWidth: 0 }}>
            <div className="spinner"></div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontWeight: 700, fontSize: '0.92rem', color: '#38bdf8', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                Extraction in Progress
                {batchProgress && (
                  <span className="progress-pill">
                    {batchProgress.current} / {batchProgress.total} Posts
                  </span>
                )}
              </div>
              <div style={{ fontSize: '0.82rem', color: '#94a3b8', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {batchProgress ? (
                  <>Scraping: <span style={{ color: '#e2e8f0' }}>{batchProgress.currentUrl}</span></>
                ) : (
                  extractStep || 'Scraping comments and extracting profiles...'
                )}
              </div>
              {batchProgress && (
                <div className="progress-bar-track">
                  <div 
                    className="progress-bar-fill" 
                    style={{ width: `${Math.round(((batchProgress.current - 1) / batchProgress.total) * 100)}%` }} 
                  />
                </div>
              )}
            </div>
          </div>
          <div style={{ fontSize: '0.78rem', color: '#64748b', textAlign: 'right', flexShrink: 0 }}>
            {batchProgress ? `${batchProgress.completed?.length || 0} done` : 'Usually completes in 10-25 seconds'}
          </div>
        </div>
      )}

      {/* Clean Fresh Starting State when no post is actively loaded */}
      {!currentPost && !isExtracting && (
        <div className="fresh-welcome-state">
          <div className="fresh-welcome-title">LeadStream Ready to Extract</div>
          <div className="fresh-welcome-desc">
            Paste any LinkedIn post URL above or upload a document with links to collect and enrich commenter leads in seconds.
          </div>
          {historyPosts.length > 0 && (
            <button
              type="button"
              className="fresh-history-prompt-btn"
              onClick={() => setIsHistoryOpen(true)}
            >
              <History size={14} color="#38bdf8" />
              <span>Browse Past Posts ({historyPosts.length} saved)</span>
            </button>
          )}
        </div>
      )}

      {/* Active Post Details & Stat Cards */}
      {currentPost && (
        <section className="animate-fade">
          {/* Executive KPI Grid */}
          <div className="kpi-grid">
            <div className="kpi-card kpi-total">
              <div className="kpi-header">
                <span className="kpi-title">Total Comments</span>
                <div className="kpi-icon-badge">
                  <MessageSquare size={14} />
                </div>
              </div>
              <div className="kpi-num">{stats.total}</div>
              <div className="kpi-subtext">
                <span>Scraped & persisted</span>
              </div>
            </div>

            <div className="kpi-card kpi-leads">
              <div className="kpi-header">
                <span className="kpi-title">Unique Leads</span>
                <div className="kpi-icon-badge">
                  <Users size={14} />
                </div>
              </div>
              <div className="kpi-num">{stats.uniqueProfiles}</div>
              <div className="kpi-subtext">
                <span style={{ color: '#8b5cf6', fontWeight: 600 }}>
                  {stats.total > 0 ? `${Math.round((stats.uniqueProfiles / stats.total) * 100)}% unique ratio` : 'Ready to analyze'}
                </span>
              </div>
            </div>

            <div className="kpi-card kpi-emails">
              <div className="kpi-header">
                <span className="kpi-title">Emails Captured</span>
                <div className="kpi-icon-badge">
                  <Mail size={14} />
                </div>
              </div>
              <div className="kpi-num">{stats.emailsCount}</div>
              <div className="kpi-subtext">
                <span style={{ color: '#10b981', fontWeight: 600 }}>
                  {stats.emailsCount > 0 ? `${stats.emailsCount} verified leads` : 'Enrichment active'}
                </span>
              </div>
            </div>

            <div className="kpi-card kpi-phones">
              <div className="kpi-header">
                <span className="kpi-title">Phone Numbers</span>
                <div className="kpi-icon-badge">
                  <Phone size={14} />
                </div>
              </div>
              <div className="kpi-num">{stats.phonesCount}</div>
              <div className="kpi-subtext">
                <span style={{ color: '#f59e0b', fontWeight: 600 }}>
                  {stats.phonesCount > 0 ? `${stats.phonesCount} direct dials` : 'Direct contact'}
                </span>
              </div>
            </div>
          </div>

          {/* Post Header & Export Action */}
          <div className="post-action-bar">
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
                <div className="post-title-text" title={currentPost.title || currentPost.canonical_url}>
                  {currentPost.title || 'Untitled Post'}
                </div>
                {currentPost.isCombined && (
                  <span style={{ fontSize: '0.72rem', padding: '0.2rem 0.5rem', borderRadius: '4px', background: 'rgba(56, 189, 248, 0.2)', color: '#38bdf8', fontWeight: 700, border: '1px solid rgba(56, 189, 248, 0.3)' }}>
                    COMBINED DATASET
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', gap: '1rem', marginTop: '0.3rem', fontSize: '0.75rem', color: 'var(--text-muted)', alignItems: 'center', flexWrap: 'wrap' }}>
                {currentPost.isCombined ? (
                  <span>Aggregated across {currentPost.posts?.length || historyPosts.length} LinkedIn posts</span>
                ) : (
                  <a
                    href={currentPost.canonical_url || currentPost.original_url}
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: 'var(--text-secondary)', display: 'inline-flex', alignItems: 'center', gap: '0.25rem', textDecoration: 'none' }}
                  >
                    View on LinkedIn <ExternalLink size={11} />
                  </a>
                )}
                <span>• {filteredComments.length} {deduplicateAuthors ? 'unique leads' : 'comments'} displayed</span>
              </div>

              {/* Combined Post Quick Jump Chips */}
              {currentPost.isCombined && (currentPost.posts || historyPosts).length > 0 && (
                <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.6rem', flexWrap: 'wrap', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.72rem', color: '#64748b', marginRight: '0.2rem' }}>Filter to Single Post:</span>
                  {(currentPost.posts || historyPosts).map(p => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => loadPost(p.id)}
                      style={{
                        background: 'rgba(30, 41, 59, 0.8)',
                        border: '1px solid rgba(255, 255, 255, 0.1)',
                        color: '#94a3b8',
                        fontSize: '0.72rem',
                        padding: '0.2rem 0.5rem',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        transition: 'all 0.15s ease'
                      }}
                      title={`View only: ${p.title}`}
                    >
                      {p.title ? (p.title.length > 25 ? p.title.substring(0, 25) + '...' : p.title) : 'Post'} ({p.total_comments_count} leads)
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <button
                type="button"
                className={`btn-quick-action ${copiedId === 'bulk-emails' ? 'active-glow' : ''}`}
                onClick={copyAllEmails}
                title="Copy all discovered email addresses from current results"
              >
                {copiedId === 'bulk-emails' ? <Check size={13} /> : <Copy size={13} />}
                <span>{copiedId === 'bulk-emails' ? 'Emails Copied!' : 'Copy Emails'}</span>
              </button>

              <div className="dropdown-container">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setIsExportDropdownOpen(prev => !prev)}
                  title="Download options: Current Post or All Data"
                >
                  <Download size={13} />
                  <span>Download</span>
                  <ChevronDown size={12} style={{ transform: isExportDropdownOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} />
                </button>

                {isExportDropdownOpen && (
                  <div className="dropdown-menu">
                    <a
                      href={buildExportUrl(currentPost.id, 'csv')}
                      download
                      className="dropdown-item"
                      onClick={() => setIsExportDropdownOpen(false)}
                    >
                      <Download size={14} color="#38bdf8" />
                      <div>
                        <div>Download Current Post (CSV)</div>
                        <div className="dropdown-item-desc">
                          {currentPost.title ? (currentPost.title.length > 30 ? currentPost.title.slice(0, 30) + '...' : currentPost.title) : 'Active post'} ({filteredComments.length} leads)
                        </div>
                      </div>
                    </a>

                    <a
                      href={buildExportUrl(currentPost.id, 'xlsx')}
                      download
                      className="dropdown-item"
                      onClick={() => setIsExportDropdownOpen(false)}
                    >
                      <FileSpreadsheet size={14} color="#10b981" />
                      <div>
                        <div>Download Current Post (Excel .xlsx)</div>
                        <div className="dropdown-item-desc">
                          Formatted Excel workbook ({filteredComments.length} leads)
                        </div>
                      </div>
                    </a>

                    <div className="dropdown-divider" />

                    <a
                      href={buildExportUrl('combined', 'csv')}
                      download
                      className="dropdown-item"
                      onClick={() => setIsExportDropdownOpen(false)}
                    >
                      <FileDown size={14} color="#a855f7" />
                      <div>
                        <div>Download All Data Combined (CSV)</div>
                        <div className="dropdown-item-desc">
                          All {historyPosts.length} posts across database
                        </div>
                      </div>
                    </a>

                    <a
                      href={buildExportUrl('combined', 'xlsx')}
                      download
                      className="dropdown-item"
                      onClick={() => setIsExportDropdownOpen(false)}
                    >
                      <FileSpreadsheet size={14} color="#f59e0b" />
                      <div>
                        <div>Download All Data Combined (Excel .xlsx)</div>
                        <div className="dropdown-item-desc">
                          Complete database workbook ({historyPosts.length} posts)
                        </div>
                      </div>
                    </a>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Filtering Toolbar */}
          <div className="toolbar">
            <input
              type="text"
              className="search-input-box"
              placeholder="Filter by commenter name, headline, email, or comment keywords..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />

            <div className="toggle-group">
              <label className="custom-checkbox" style={{ color: deduplicateAuthors ? '#38bdf8' : 'inherit', fontWeight: deduplicateAuthors ? 700 : 'normal' }}>
                <input
                  type="checkbox"
                  checked={deduplicateAuthors}
                  onChange={(e) => setDeduplicateAuthors(e.target.checked)}
                />
                Unique Vendors Only
              </label>

              <label className="custom-checkbox">
                <input
                  type="checkbox"
                  checked={excludeAuthorReplies}
                  onChange={(e) => setExcludeAuthorReplies(e.target.checked)}
                />
                Exclude Author Replies
              </label>

              <label className="custom-checkbox">
                <input
                  type="checkbox"
                  checked={contactOnly}
                  onChange={(e) => setContactOnly(e.target.checked)}
                />
                Contact Info Only
              </label>

              <div className="view-segment">
                <button
                  type="button"
                  className={`view-btn ${activeView === 'table' ? 'active' : ''}`}
                  onClick={() => setActiveView('table')}
                  title="Table View"
                >
                  <List size={16} />
                </button>
                <button
                  type="button"
                  className={`view-btn ${activeView === 'cards' ? 'active' : ''}`}
                  onClick={() => setActiveView('cards')}
                  title="Lead Cards View"
                >
                  <LayoutGrid size={16} />
                </button>
              </div>
            </div>
          </div>

          {/* View Container */}
          {filteredComments.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '4rem 2rem', background: 'var(--bg-card)', borderRadius: 'var(--radius-lg)', border: '1px solid var(--border-color)' }}>
              <Users size={48} color="#475569" style={{ margin: '0 auto 1rem auto' }} />
              <h3 style={{ fontSize: '1.1rem', fontWeight: 700, marginBottom: '0.4rem' }}>No comments match your active filters</h3>
              <p style={{ color: '#94a3b8', fontSize: '0.9rem' }}>
                Try clearing the search query or unchecking "Contact Info Only".
              </p>
            </div>
          ) : activeView === 'table' ? (
            /* High Density Enterprise Table */
            <div className="table-container animate-fade">
              <table className="leads-table">
                <thead>
                  <tr>
                    <th>Lead Name</th>
                    <th>LinkedIn Profile</th>
                    {currentPost.isCombined && <th>Source Post</th>}
                    <th>Headline</th>
                    <th>Email</th>
                    <th>Company Website</th>
                    <th>Company LinkedIn Link</th>
                    <th>Phone</th>
                    <th>Comment Text</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredComments.map((comment) => (
                    <tr
                      key={comment.id}
                      onClick={() => setSelectedLead(comment)}
                      className={selectedLead && selectedLead.id === comment.id ? 'selected' : ''}
                      title="Click row to open Lead Detail Drawer"
                    >
                      <td>
                        <div className="lead-name-cell">
                          <div className="avatar-initials">
                            {getInitials(comment.author_name)}
                          </div>
                          <div>
                            <div className="lead-name-primary">
                              {comment.author_name || 'Anonymous User'}
                              {comment.depth > 0 && (
                                <span className="badge-reply">Reply</span>
                              )}
                              {deduplicateAuthors && comment.commentCount > 1 && (
                                <span style={{ marginLeft: '0.4rem', fontSize: '0.72rem', padding: '0.15rem 0.45rem', borderRadius: '4px', background: 'rgba(56, 189, 248, 0.15)', color: '#38bdf8', border: '1px solid rgba(56, 189, 248, 0.3)' }}>
                                  {comment.commentCount} comments
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                      </td>

                      <td>
                        {comment.author_profile_url ? (
                          <a
                            href={comment.author_profile_url}
                            target="_blank"
                            rel="noreferrer"
                            className="lead-profile-link"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <span>Profile</span>
                            <ExternalLink size={12} />
                          </a>
                        ) : (
                          <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>None</span>
                        )}
                      </td>

                      {currentPost.isCombined && (
                        <td style={{ maxWidth: '160px', fontSize: '0.78rem' }}>
                          <span 
                            style={{ 
                              display: 'inline-block',
                              maxWidth: '150px',
                              overflow: 'hidden', 
                              textOverflow: 'ellipsis', 
                              whiteSpace: 'nowrap',
                              padding: '0.15rem 0.4rem', 
                              borderRadius: '4px', 
                              background: 'var(--bg-subtle)', 
                              color: 'var(--text-secondary)' 
                            }} 
                            title={comment.post_title || ''}
                          >
                            {comment.post_title ? (comment.post_title.length > 20 ? comment.post_title.substring(0, 20) + '...' : comment.post_title) : 'Post'}
                          </span>
                        </td>
                      )}

                      <td style={{ maxWidth: '240px', color: 'var(--text-secondary)', fontSize: '0.82rem' }}>
                        <div style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={comment.author_headline || ''}>
                          {comment.author_headline || '—'}
                        </div>
                      </td>

                      <td>
                        {comment.email ? (
                          <button
                            type="button"
                            className="pill-email"
                            onClick={(e) => {
                              e.stopPropagation();
                              copyToClipboard(comment.email, `email-${comment.id}`);
                            }}
                            title="Click to copy email"
                          >
                            {copiedId === `email-${comment.id}` ? <Check size={12} /> : <Copy size={12} />}
                            <span>{comment.email}</span>
                          </button>
                        ) : (
                          <span style={{ color: 'var(--text-muted)' }}>—</span>
                        )}
                      </td>

                      <td>
                        {(() => {
                          const site = getCompanyWebsite(comment.company_website, comment.company_linkedin_url, comment.email, comment.comment_text);
                          if (site) {
                            return (
                              <a
                                href={site.url}
                                target="_blank"
                                rel="noreferrer"
                                className="company-badge-web"
                                onClick={(e) => e.stopPropagation()}
                                title={`Visit official website: ${site.url}`}
                              >
                                <span>{site.display}</span>
                                <ExternalLink size={11} />
                              </a>
                            );
                          }
                          return <span style={{ color: 'var(--text-muted)' }}>—</span>;
                        })()}
                      </td>

                      <td>
                        {(() => {
                          const site = getCompanyWebsite(comment.company_website, comment.company_linkedin_url, comment.email, comment.comment_text);
                          const li = getCompanyLinkedIn(comment.company_linkedin_url, comment.email, site?.url);
                          if (li) {
                            return (
                              <a
                                href={li.url}
                                target="_blank"
                                rel="noreferrer"
                                className="company-badge-li"
                                onClick={(e) => e.stopPropagation()}
                                title={`Visit official LinkedIn company page: ${li.url}`}
                              >
                                <span>{li.display}</span>
                                <ExternalLink size={11} />
                              </a>
                            );
                          }
                          return <span style={{ color: 'var(--text-muted)' }}>—</span>;
                        })()}
                      </td>

                      <td>
                        {comment.phone ? (
                          <button
                            type="button"
                            className="pill-phone"
                            onClick={(e) => {
                              e.stopPropagation();
                              copyToClipboard(comment.phone, `phone-${comment.id}`);
                            }}
                            title="Click to copy phone"
                          >
                            {copiedId === `phone-${comment.id}` ? <Check size={12} /> : <Copy size={12} />}
                            <span>{comment.phone}</span>
                          </button>
                        ) : (
                          <span style={{ color: 'var(--text-muted)' }}>—</span>
                        )}
                      </td>

                      <td style={{ maxWidth: '380px' }}>
                        <div
                          style={{
                            color: 'var(--text-secondary)',
                            fontSize: '0.82rem',
                            display: '-webkit-box',
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: 'vertical',
                            overflow: 'hidden'
                          }}
                          title={deduplicateAuthors && comment.allComments ? comment.allComments.join(' | ') : comment.comment_text}
                        >
                          {deduplicateAuthors && comment.allComments ? comment.allComments.join(' | ') : comment.comment_text}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            /* Interactive Lead Cards View */
            <div className="cards-grid animate-fade">
              {filteredComments.map((comment) => (
                <div key={comment.id} className="lead-card-box">
                  <div>
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '0.8rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                        <div className="avatar-initials" style={{ width: '42px', height: '42px', fontSize: '0.95rem' }}>
                          {getInitials(comment.author_name)}
                        </div>
                        <div>
                          <div style={{ fontWeight: 800, fontSize: '0.95rem', color: '#ffffff' }}>
                            {comment.author_name || 'Anonymous User'}
                            {comment.depth > 0 && <span className="badge-reply">Reply</span>}
                          </div>
                          <div style={{ fontSize: '0.78rem', color: '#94a3b8', marginTop: '2px' }}>
                            {comment.author_headline || 'LinkedIn Member'}
                          </div>
                        </div>
                      </div>

                      {comment.author_profile_url && (
                        <a
                          href={comment.author_profile_url}
                          target="_blank"
                          rel="noreferrer"
                          className="btn-secondary"
                          style={{ padding: '0.35rem 0.6rem', fontSize: '0.75rem' }}
                        >
                          <ExternalLink size={13} />
                        </a>
                      )}
                    </div>

                    {/* Contact Pills */}
                    {(comment.email || comment.phone || comment.company_linkedin_url) && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginTop: '0.8rem' }}>
                        {comment.email && (
                          <button
                            type="button"
                            className="pill-email"
                            onClick={() => copyToClipboard(comment.email, `email-c-${comment.id}`)}
                          >
                            {copiedId === `email-c-${comment.id}` ? <Check size={12} /> : <Copy size={12} />}
                            {comment.email}
                          </button>
                        )}
                        {(() => {
                          const site = getCompanyWebsite(comment.company_website, comment.company_linkedin_url, comment.email, comment.comment_text);
                          if (!site) return null;
                          return (
                            <a
                              href={site.url}
                              target="_blank"
                              rel="noreferrer"
                              className="pill-email"
                              style={{ textDecoration: 'none', background: 'rgba(56, 189, 248, 0.1)', border: '1px solid rgba(56, 189, 248, 0.25)', color: '#38bdf8' }}
                              title={`Visit official website: ${site.url}`}
                            >
                              <ExternalLink size={12} />
                              {site.display}
                            </a>
                          );
                        })()}
                        {(() => {
                          const site = getCompanyWebsite(comment.company_website, comment.company_linkedin_url, comment.email, comment.comment_text);
                          const li = getCompanyLinkedIn(comment.company_linkedin_url, comment.email, site?.url);
                          if (!li) return null;
                          return (
                            <a
                              href={li.url}
                              target="_blank"
                              rel="noreferrer"
                              className="pill-email"
                              style={{ textDecoration: 'none', background: 'rgba(10, 102, 194, 0.15)', border: '1px solid rgba(10, 102, 194, 0.4)', color: '#60a5fa' }}
                              title={`Visit official LinkedIn company page: ${li.url}`}
                            >
                              <ExternalLink size={12} />
                              {li.display}
                            </a>
                          );
                        })()}
                        {comment.phone && (
                          <button
                            type="button"
                            className="pill-phone"
                            onClick={() => copyToClipboard(comment.phone, `phone-c-${comment.id}`)}
                          >
                            {copiedId === `phone-c-${comment.id}` ? <Check size={12} /> : <Copy size={12} />}
                            {comment.phone}
                          </button>
                        )}
                      </div>
                    )}

                    {/* Comment text */}
                    <div className="comment-bubble">
                      "{comment.comment_text}"
                    </div>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '1rem', paddingTop: '0.8rem', borderTop: '1px solid rgba(255,255,255,0.06)', fontSize: '0.75rem', color: '#64748b' }}>
                    <span>Reactions: {comment.reaction_count || 0}</span>
                    <span>{comment.commented_at ? new Date(comment.commented_at).toLocaleDateString() : 'Recent'}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* History Drawer Modal */}
      {isHistoryOpen && (
        <div className="modal-overlay" onClick={() => setIsHistoryOpen(false)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                <History size={20} color="#38bdf8" />
                <h2 style={{ fontSize: '1.2rem', fontWeight: 800 }}>Collection History</h2>
              </div>
              <button
                type="button"
                className="btn-secondary"
                style={{ padding: '0.35rem' }}
                onClick={() => setIsHistoryOpen(false)}
              >
                <X size={18} />
              </button>
            </div>

            <p style={{ color: '#94a3b8', fontSize: '0.85rem', marginBottom: '1.2rem' }}>
              Past LinkedIn posts collected into your SQLite database. Click any run to review without re-scraping.
            </p>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
              {historyPosts.length > 1 && (
                <div
                  onClick={() => loadCombinedView()}
                  style={{
                    background: currentPost?.id === 'combined' ? 'rgba(56, 189, 248, 0.2)' : 'linear-gradient(135deg, rgba(56, 189, 248, 0.1), rgba(99, 102, 241, 0.1))',
                    border: currentPost?.id === 'combined' ? '1px solid #38bdf8' : '1px solid rgba(56, 189, 248, 0.3)',
                    borderRadius: 'var(--radius-md)',
                    padding: '1rem',
                    cursor: 'pointer',
                    transition: 'all 0.2s ease',
                    marginBottom: '0.5rem'
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <Layers size={16} color="#38bdf8" />
                      <span style={{ fontWeight: 800, fontSize: '0.92rem', color: '#f8fafc' }}>
                        All Scraped Posts Combined
                      </span>
                    </div>
                    <span style={{ fontSize: '0.75rem', fontWeight: 700, padding: '0.15rem 0.5rem', borderRadius: '4px', background: 'rgba(56, 189, 248, 0.2)', color: '#38bdf8' }}>
                      {historyPosts.reduce((acc, p) => acc + (p.total_comments_count || 0), 0)} TOTAL LEADS
                    </span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.5rem' }}>
                    <div style={{ fontSize: '0.78rem', color: '#94a3b8' }}>
                      View all {historyPosts.length} posts together with unified vendor deduplication
                    </div>
                    <a
                      href={`/api/export/combined${deduplicateAuthors ? '?deduplicate_authors=true' : ''}`}
                      download
                      onClick={(e) => e.stopPropagation()}
                      className="history-item-download-btn"
                      title="Download complete database leads CSV"
                    >
                      <FileDown size={12} />
                      <span>Download All</span>
                    </a>
                  </div>
                </div>
              )}
              {historyPosts.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '3rem 1rem', color: '#64748b' }}>
                  No past collection runs found.
                </div>
              ) : (
                historyPosts.map((post) => (
                  <div
                    key={post.id}
                    onClick={() => loadPost(post.id)}
                    style={{
                      background: currentPost && currentPost.id === post.id ? 'rgba(14, 165, 233, 0.15)' : 'rgba(30, 41, 59, 0.5)',
                      border: currentPost && currentPost.id === post.id ? '1px solid rgba(14, 165, 233, 0.4)' : '1px solid rgba(255, 255, 255, 0.08)',
                      borderRadius: 'var(--radius-md)',
                      padding: '1rem',
                      cursor: 'pointer',
                      transition: 'all 0.2s ease',
                      position: 'relative'
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '0.5rem' }}>
                      <div style={{ fontWeight: 700, fontSize: '0.9rem', color: '#f8fafc', maxWidth: '340px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {post.title || post.canonical_url || 'LinkedIn Post'}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                        <a
                          href={`/api/export/${post.id}${deduplicateAuthors ? '?deduplicate_authors=true' : ''}`}
                          download
                          onClick={(e) => e.stopPropagation()}
                          className="history-item-download-btn"
                          title="Directly download this post CSV"
                        >
                          <Download size={12} />
                          <span>CSV</span>
                        </a>
                      </div>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginTop: '0.6rem', fontSize: '0.78rem', color: '#94a3b8' }}>
                      <span style={{ color: '#38bdf8', fontWeight: 600 }}>
                        {post.total_comments_count} comments
                      </span>
                      <span>
                        {post.updated_at ? new Date(post.updated_at).toLocaleString() : ''}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {/* Interactive Lead Detail Drawer */}
      {selectedLead && (
        <div className="modal-overlay" onClick={() => setSelectedLead(null)}>
          <div className="drawer lead-drawer" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.25rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <div className="avatar-initials" style={{ width: '40px', height: '40px', fontSize: '0.95rem' }}>
                  {getInitials(selectedLead.author_name)}
                </div>
                <div>
                  <h3 style={{ fontSize: '1.1rem', fontWeight: 800, color: 'var(--text-primary)', margin: 0 }}>
                    {selectedLead.author_name || 'Anonymous User'}
                  </h3>
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginTop: '2px' }}>
                    {selectedLead.author_headline || 'LinkedIn Member'}
                  </div>
                </div>
              </div>
              <button
                type="button"
                className="btn-secondary"
                style={{ padding: '0.35rem' }}
                onClick={() => setSelectedLead(null)}
              >
                <X size={18} />
              </button>
            </div>

            {/* Quick Actions Bar */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1.5rem', paddingBottom: '1rem', borderBottom: '1px solid var(--border-color)' }}>
              {selectedLead.author_profile_url && (
                <a
                  href={selectedLead.author_profile_url}
                  target="_blank"
                  rel="noreferrer"
                  className="lead-profile-link"
                >
                  <ExternalLink size={13} />
                  <span>View LinkedIn Profile</span>
                </a>
              )}
              {selectedLead.email && (
                <button
                  type="button"
                  className="btn-quick-action"
                  onClick={() => copyToClipboard(selectedLead.email, `drawer-email`)}
                >
                  {copiedId === 'drawer-email' ? <Check size={13} /> : <Copy size={13} />}
                  <span>{selectedLead.email}</span>
                </button>
              )}
              {selectedLead.phone && (
                <button
                  type="button"
                  className="btn-quick-action"
                  onClick={() => copyToClipboard(selectedLead.phone, `drawer-phone`)}
                >
                  {copiedId === 'drawer-phone' ? <Check size={13} /> : <Copy size={13} />}
                  <span>{selectedLead.phone}</span>
                </button>
              )}
            </div>

            {/* Detailed Metadata Cards */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div>
                <label style={{ fontSize: '0.72rem', textTransform: 'uppercase', fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.04em' }}>
                  Company Enrichment
                </label>
                <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.4rem', flexWrap: 'wrap' }}>
                  {(() => {
                    const site = getCompanyWebsite(selectedLead.company_website, selectedLead.company_linkedin_url, selectedLead.email, selectedLead.comment_text);
                    if (site) {
                      return (
                        <a
                          href={site.url}
                          target="_blank"
                          rel="noreferrer"
                          className="company-badge-web"
                        >
                          <ExternalLink size={12} />
                          <span>Website: {site.display}</span>
                        </a>
                      );
                    }
                    return null;
                  })()}

                  {(() => {
                    const site = getCompanyWebsite(selectedLead.company_website, selectedLead.company_linkedin_url, selectedLead.email, selectedLead.comment_text);
                    const li = getCompanyLinkedIn(selectedLead.company_linkedin_url, selectedLead.email, site?.url);
                    if (li) {
                      return (
                        <a
                          href={li.url}
                          target="_blank"
                          rel="noreferrer"
                          className="company-badge-li"
                        >
                          <ExternalLink size={12} />
                          <span>Company LinkedIn: {li.display}</span>
                        </a>
                      );
                    }
                    return null;
                  })()}

                  {!selectedLead.company_website && !selectedLead.company_linkedin_url && (
                    <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>No domain found</span>
                  )}
                </div>
              </div>

              <div>
                <label style={{ fontSize: '0.72rem', textTransform: 'uppercase', fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.04em' }}>
                  Full Comment Content
                </label>
                <div style={{
                  background: 'var(--bg-primary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '1rem',
                  marginTop: '0.4rem',
                  fontSize: '0.86rem',
                  color: 'var(--text-primary)',
                  lineHeight: '1.5',
                  whiteSpace: 'pre-wrap'
                }}>
                  {deduplicateAuthors && selectedLead.allComments ? selectedLead.allComments.join('\n\n---\n\n') : selectedLead.comment_text}
                </div>
              </div>

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.5rem', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                <span>Reactions: {selectedLead.reaction_count || 0}</span>
                <span>Commented: {selectedLead.commented_at ? new Date(selectedLead.commented_at).toLocaleString() : 'Recent'}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Toast Feedback */}
      {toastMessage && (
        <div className="toast-bar animate-fade">
          <CheckCircle2 size={18} color="#34d399" />
          <span>{toastMessage}</span>
        </div>
      )}
    </div>
  );
}
