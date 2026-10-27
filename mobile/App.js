import React, { useState, useRef } from 'react';
import {
  StyleSheet,
  View,
  SafeAreaView,
  StatusBar,
  ActivityIndicator,
  Text,
  TouchableOpacity,
  TextInput,
  Platform,
  Alert
} from 'react-native';
import { WebView } from 'react-native-webview';

const DEFAULT_SERVER_URL = 'http://192.168.1.8:5001';

export default function App() {
  const [currentUrl, setCurrentUrl] = useState(DEFAULT_SERVER_URL);
  const [inputUrl, setInputUrl] = useState(DEFAULT_SERVER_URL);
  const [showConfig, setShowConfig] = useState(false);
  const [loading, setLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const webViewRef = useRef(null);

  const handleReload = () => {
    setHasError(false);
    setLoading(true);
    if (webViewRef.current) {
      webViewRef.current.reload();
    }
  };

  const handleSaveUrl = () => {
    let formatted = inputUrl.trim();
    if (!formatted.startsWith('http://') && !formatted.startsWith('https://')) {
      formatted = 'http://' + formatted;
    }
    setCurrentUrl(formatted);
    setShowConfig(false);
    setHasError(false);
    setLoading(true);
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar barStyle="light-content" backgroundColor="#0F172A" />

      {/* Top Floating Control Bar */}
      <View style={styles.topBar}>
        <View style={styles.statusIndicator}>
          <View style={[styles.statusDot, { backgroundColor: hasError ? '#EF4444' : '#10B981' }]} />
          <Text style={styles.statusText} numberOfLines={1}>
            {currentUrl}
          </Text>
        </View>

        <View style={styles.buttonGroup}>
          <TouchableOpacity style={styles.iconButton} onPress={handleReload}>
            <Text style={styles.iconButtonText}>🔄</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.configButton} onPress={() => setShowConfig(!showConfig)}>
            <Text style={styles.configButtonText}>⚙️ IP</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* IP Configuration Banner Modal */}
      {showConfig && (
        <View style={styles.configCard}>
          <Text style={styles.configTitle}>Backend Server Address</Text>
          <Text style={styles.configSubtitle}>
            Ensure your phone and PC are connected to the same Wi-Fi.
          </Text>
          <TextInput
            style={styles.input}
            value={inputUrl}
            onChangeText={setInputUrl}
            placeholder="http://192.168.1.7:5001"
            placeholderTextColor="#94A3B8"
            autoCapitalize="none"
            autoCorrect={false}
          />
          <View style={styles.configActionRow}>
            <TouchableOpacity
              style={[styles.actionBtn, styles.cancelBtn]}
              onPress={() => setShowConfig(false)}
            >
              <Text style={styles.actionBtnText}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.actionBtn, styles.saveBtn]}
              onPress={handleSaveUrl}
            >
              <Text style={[styles.actionBtnText, { color: '#FFFFFF', fontWeight: 'bold' }]}>
                Connect
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      {/* Main WebView Container */}
      <View style={styles.webContainer}>
        <WebView
          ref={webViewRef}
          source={{ uri: currentUrl }}
          style={styles.webView}
          javaScriptEnabled={true}
          domStorageEnabled={true}
          startInLoadingState={true}
          scalesPageToFit={true}
          allowsInlineMediaPlayback={true}
          mediaPlaybackRequiresUserAction={false}
          allowsBackForwardNavigationGestures={true}
          allowFileAccess={true}
          allowFileAccessFromFileURLs={true}
          allowUniversalAccessFromFileURLs={true}
          originWhitelist={['*']}
          onLoadStart={() => {
            setLoading(true);
            setHasError(false);
          }}
          onLoadEnd={() => setLoading(false)}
          onError={(syntheticEvent) => {
            const { nativeEvent } = syntheticEvent;
            setLoading(false);
            setHasError(true);
            Alert.alert(
              "Connection Issue",
              `Cannot reach ${currentUrl}.\n\nCheck that:\n1. Your phone is on the same Wi-Fi.\n2. Flask server is running.\n3. Tap ⚙️ IP above if your computer's IP changed.`,
              [{ text: "OK" }]
            );
          }}
        />

        {loading && (
          <View style={styles.loaderOverlay}>
            <ActivityIndicator size="large" color="#0284C7" />
            <Text style={styles.loaderText}>Connecting to Ink 2 Insight...</Text>
          </View>
        )}

        {hasError && (
          <View style={styles.errorOverlay}>
            <Text style={styles.errorIcon}>📡</Text>
            <Text style={styles.errorTitle}>Cannot Connect to Server</Text>
            <Text style={styles.errorDetails}>
              Could not reach {currentUrl}
            </Text>
            <TouchableOpacity style={styles.retryBtn} onPress={handleReload}>
              <Text style={styles.retryBtnText}>Retry Connection</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.retryBtn, { backgroundColor: '#334155', marginTop: 10 }]}
              onPress={() => setShowConfig(true)}
            >
              <Text style={styles.retryBtnText}>Change IP Address</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#0F172A',
    paddingTop: Platform.OS === 'android' ? StatusBar.currentHeight : 0,
  },
  topBar: {
    height: 44,
    backgroundColor: '#0F172A',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#1E293B',
  },
  statusIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
    marginRight: 8,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 8,
  },
  statusText: {
    color: '#94A3B8',
    fontSize: 12,
    fontWeight: '600',
  },
  buttonGroup: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  iconButton: {
    padding: 6,
    marginRight: 6,
  },
  iconButtonText: {
    fontSize: 14,
  },
  configButton: {
    backgroundColor: '#1E293B',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: '#334155',
  },
  configButtonText: {
    color: '#38BDF8',
    fontSize: 11,
    fontWeight: '700',
  },
  configCard: {
    backgroundColor: '#1E293B',
    margin: 12,
    padding: 14,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#38BDF8',
    elevation: 6,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 6,
  },
  configTitle: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: 'bold',
    marginBottom: 4,
  },
  configSubtitle: {
    color: '#94A3B8',
    fontSize: 11,
    marginBottom: 10,
  },
  input: {
    backgroundColor: '#0F172A',
    color: '#F8FAFC',
    borderWidth: 1,
    borderColor: '#334155',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 13,
    marginBottom: 12,
  },
  configActionRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 8,
  },
  actionBtn: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: 6,
  },
  cancelBtn: {
    backgroundColor: '#334155',
  },
  saveBtn: {
    backgroundColor: '#0284C7',
  },
  actionBtnText: {
    color: '#CBD5E1',
    fontSize: 12,
    fontWeight: '600',
  },
  webContainer: {
    flex: 1,
    backgroundColor: '#0F172A',
    position: 'relative',
  },
  webView: {
    flex: 1,
    backgroundColor: '#0F172A',
  },
  loaderOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: '#0F172A',
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 10,
  },
  loaderText: {
    color: '#94A3B8',
    fontSize: 13,
    marginTop: 12,
    fontWeight: '500',
  },
  errorOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: '#0F172A',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
    zIndex: 20,
  },
  errorIcon: {
    fontSize: 48,
    marginBottom: 12,
  },
  errorTitle: {
    color: '#F8FAFC',
    fontSize: 18,
    fontWeight: 'bold',
    marginBottom: 8,
  },
  errorDetails: {
    color: '#94A3B8',
    fontSize: 13,
    textAlign: 'center',
    marginBottom: 20,
  },
  retryBtn: {
    backgroundColor: '#0284C7',
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
    minWidth: 160,
    alignItems: 'center',
  },
  retryBtnText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
    fontSize: 14,
  },
});
