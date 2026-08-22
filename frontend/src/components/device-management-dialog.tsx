import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { QRCodeSVG } from 'qrcode.react'
import { Loader2, QrCode, Smartphone, TabletSmartphone, TriangleAlert, X } from 'lucide-react'
import { devices as devicesApi } from '@/lib/api'
import type { DevicePairing, PairedDevice } from '@/types'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface DeviceManagementDialogProps {
  open: boolean
  onClose: () => void
}

const PAIRING_URL_STORAGE_KEY = 'device_pairing_url'
const LIST_REFRESH_MS = 15_000
const PAIRING_POLL_MS = 2_000

function defaultInstanceUrl(): string {
  return localStorage.getItem(PAIRING_URL_STORAGE_KEY) || window.location.origin
}

export function DeviceManagementDialog({ open, onClose }: DeviceManagementDialogProps) {
  const { t } = useTranslation()
  const [devices, setDevices] = useState<PairedDevice[]>([])
  const [loading, setLoading] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)
  const [revokingId, setRevokingId] = useState<string | null>(null)
  const [confirmRevokeId, setConfirmRevokeId] = useState<string | null>(null)
  const [pairing, setPairing] = useState<DevicePairing | null>(null)
  const [creatingPairing, setCreatingPairing] = useState(false)
  const [instanceUrl, setInstanceUrl] = useState(defaultInstanceUrl)
  // The pairing code expires server-side; track it so the QR view can offer a retry.
  const [pairingExpired, setPairingExpired] = useState(false)
  const pairingRef = useRef<DevicePairing | null>(null)
  pairingRef.current = pairing

  const loadDevices = useCallback(async () => {
    setLoadFailed(false)
    try {
      setDevices(await devicesApi.list())
    } catch {
      setLoadFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    setLoading(true)
    void loadDevices()
    const interval = setInterval(() => void loadDevices(), LIST_REFRESH_MS)
    return () => clearInterval(interval)
  }, [open, loadDevices])

  useEffect(() => {
    if (!open || !pairing) return
    const interval = setInterval(async () => {
      const current = pairingRef.current
      if (!current) return
      try {
        const status = await devicesApi.pairingStatus(current.pairing_id)
        if (status.status === 'claimed') {
          toast.success(t('devices.paired', { name: status.device_name ?? '' }))
          setPairing(null)
          void loadDevices()
        }
      } catch {
        // 404 → the code expired before a device claimed it.
        setPairingExpired(true)
        setPairing(null)
      }
    }, PAIRING_POLL_MS)
    return () => clearInterval(interval)
  }, [open, pairing, loadDevices, t])

  const handleClose = () => {
    setPairing(null)
    setPairingExpired(false)
    setConfirmRevokeId(null)
    onClose()
  }

  const handleStartPairing = async () => {
    setCreatingPairing(true)
    setPairingExpired(false)
    try {
      localStorage.setItem(PAIRING_URL_STORAGE_KEY, instanceUrl)
      setPairing(await devicesApi.createPairing())
    } catch {
      toast.error(t('devices.pairingError'))
    } finally {
      setCreatingPairing(false)
    }
  }

  const handleRevoke = async (device: PairedDevice) => {
    setRevokingId(device.id)
    try {
      await devicesApi.revoke(device.id)
      setDevices((current) => current.filter((item) => item.id !== device.id))
      setConfirmRevokeId(null)
      toast.success(t('devices.revoked'))
    } catch {
      toast.error(t('devices.revokeError'))
    } finally {
      setRevokingId(null)
    }
  }

  const formatLastSeen = (device: PairedDevice) => {
    if (device.connected) return t('devices.connected')
    if (!device.last_seen_at) return t('devices.neverSeen')
    return t('devices.lastSeen', {
      when: new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(
        new Date(device.last_seen_at),
      ),
    })
  }

  const qrPayload = pairing
    ? JSON.stringify({ v: 1, url: instanceUrl, code: pairing.code })
    : null

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('devices.title')}</DialogTitle>
        </DialogHeader>

        {pairing && qrPayload ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">{t('devices.scanDescription')}</p>
            <div className="flex justify-center rounded-lg border bg-white p-4">
              <QRCodeSVG value={qrPayload} size={200} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="instance-url">{t('devices.instanceUrl')}</Label>
              <Input id="instance-url" value={instanceUrl} readOnly />
              <p className="text-xs text-muted-foreground">{t('devices.instanceUrlLockedHint')}</p>
            </div>
            <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 size={14} className="animate-spin" />
              {t('devices.waitingForDevice')}
            </div>
            <Button type="button" variant="outline" className="w-full" onClick={() => setPairing(null)}>
              {t('common.cancel')}
            </Button>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">{t('devices.description')}</p>

            <div className="space-y-3 rounded-lg border p-3">
              <div className="space-y-1.5">
                <Label htmlFor="instance-url">{t('devices.instanceUrl')}</Label>
                <Input
                  id="instance-url"
                  value={instanceUrl}
                  onChange={(event) => setInstanceUrl(event.target.value)}
                  placeholder="https://securo.example.com"
                />
                <p className="text-xs text-muted-foreground">{t('devices.instanceUrlHint')}</p>
              </div>
              {instanceUrl.startsWith('http://') && (
                <div className="flex items-start gap-2.5 rounded-lg bg-amber-500/10 px-3 py-2.5 text-sm text-amber-700 dark:text-amber-300">
                  <TriangleAlert size={16} className="mt-0.5 shrink-0" />
                  <p>{t('devices.insecureUrlWarning')}</p>
                </div>
              )}
              {pairingExpired && (
                <p className="text-sm text-muted-foreground">{t('devices.pairingExpired')}</p>
              )}
              <Button
                type="button"
                onClick={() => void handleStartPairing()}
                disabled={creatingPairing || !instanceUrl.trim()}
                className="w-full"
              >
                {creatingPairing ? (
                  <Loader2 size={15} className="animate-spin" />
                ) : (
                  <QrCode size={15} />
                )}
                {t('devices.pairNew')}
              </Button>
            </div>

            <div className="space-y-2">
              {loading ? (
                <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
              ) : loadFailed ? (
                <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 p-3">
                  <p className="text-sm text-destructive">{t('devices.loadError')}</p>
                  <Button type="button" variant="outline" size="sm" onClick={() => void loadDevices()}>
                    {t('common.retry')}
                  </Button>
                </div>
              ) : devices.length === 0 ? (
                <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed px-3 py-6 text-center">
                  <TabletSmartphone size={20} className="text-muted-foreground" />
                  <p className="text-sm text-muted-foreground">{t('devices.noDevices')}</p>
                </div>
              ) : (
                devices.map((device) => {
                  const isConfirming = confirmRevokeId === device.id
                  const isRevoking = revokingId === device.id

                  return (
                    <div key={device.id} className="flex items-start gap-3 rounded-lg border p-3">
                      <div className="mt-0.5 rounded-full bg-primary/10 p-2 text-primary">
                        <Smartphone size={16} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <p className="truncate text-sm font-medium">{device.name}</p>
                          {device.connected && (
                            <span className="flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-600 dark:text-emerald-400">
                              <span className="size-1.5 rounded-full bg-emerald-500" />
                              {t('devices.connectedBadge')}
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground">{formatLastSeen(device)}</p>
                      </div>
                      {isConfirming ? (
                        <div className="flex shrink-0 items-center gap-1">
                          <Button
                            type="button"
                            variant="destructive"
                            size="sm"
                            onClick={() => void handleRevoke(device)}
                            disabled={isRevoking}
                          >
                            {isRevoking ? <Loader2 size={13} className="animate-spin" /> : t('devices.revoke')}
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => setConfirmRevokeId(null)}
                            disabled={isRevoking}
                            aria-label={t('common.cancel')}
                          >
                            <X size={14} />
                          </Button>
                        </div>
                      ) : (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="shrink-0 text-muted-foreground hover:text-destructive"
                          onClick={() => setConfirmRevokeId(device.id)}
                          aria-label={t('devices.revoke')}
                        >
                          {t('devices.revoke')}
                        </Button>
                      )}
                    </div>
                  )
                })
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={handleClose}>
            {t('common.close')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
