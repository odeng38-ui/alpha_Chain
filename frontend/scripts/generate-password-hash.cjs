/**
 * Secure Admin Password Hash Generator
 *
 * Derives a secure salt:key pair using crypto.scrypt without exposing the password
 * in command-line arguments, shell history, or process lists.
 *
 * Usage:
 *   node frontend/scripts/generate-password-hash.cjs
 */

const readline = require('node:readline');
const crypto = require('node:crypto');

function askPasswordHidden(query) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });

    const stdin = process.stdin;
    process.stdout.write(query);

    let password = '';
    const onData = (char) => {
      char = char.toString('utf8');
      switch (char) {
        case '\n':
        case '\r':
        case '\u0004':
          stdin.removeListener('data', onData);
          break;
        default:
          process.stdout.write('*');
          password += char;
          break;
      }
    };

    if (stdin.isTTY) {
      stdin.on('data', onData);
    }

    rl.question('', (answer) => {
      rl.close();
      const finalPass = stdin.isTTY ? password : answer;
      process.stdout.write('\n');
      resolve(finalPass.trim());
    });
  });
}

async function main() {
  console.log('=== ALPHA CHAIN 관리자 비밀번호 해시 생성기 (scrypt) ===');
  console.log('※ 입력하는 비밀번호는 화면 및 셸 명령 기록에 노출되지 않습니다.\n');

  const password = await askPasswordHidden('설정할 관리자 비밀번호를 입력하세요: ');

  if (!password || password.length < 8) {
    console.error('오류: 비밀번호는 최소 8자 이상이어야 합니다.');
    process.exit(1);
  }

  const salt = crypto.randomBytes(16);
  const derivedKey = crypto.scryptSync(password, salt, 64);
  const hashString = `${salt.toString('hex')}:${derivedKey.toString('hex')}`;

  console.log('\n성공적으로 생성되었습니다.');
  console.log('서버 환경변수에 다음 항목을 등록하세요:\n');
  console.log(`ADMIN_PASSWORD_HASH=${hashString}\n`);
  console.log('주의: 생성된 해시 문자열만 환경변수에 등록하고 비밀번호 원문은 저장하지 마세요.');
}

if (require.main === module) {
  main().catch((err) => {
    console.error('해시 생성 실패:', err.message);
    process.exit(1);
  });
}

module.exports = { askPasswordHidden };
